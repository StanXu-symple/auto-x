import asyncio
import hashlib
import hmac
import json
import os
import time
from collections import deque
from contextlib import asynccontextmanager, suppress

import httpx
import jwt
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.control_plane.config import read_secret
from app.control_plane.contracts import ServiceTokenResponse
from app.control_plane.nacos import NacosClient, advertise_identity, heartbeat_loop
from app.control_plane.security import ISSUER


class TokenRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    client_secret: SecretStr = Field(min_length=32, max_length=256)
    audience: str = Field(min_length=1, max_length=128)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.private_key = read_secret("SERVICE_AUTH_PRIVATE_KEY_FILE")
        # Fail closed on invalid provisioning before becoming ready.
        jwt.encode({"check": True}, app.state.private_key, algorithm="RS256")
        app.state.clients = json.loads(read_secret("SERVICE_AUTH_CLIENTS_FILE"))
        app.state.requests = deque()
        nacos = None
        beat = None
        if os.environ.get("NACOS_SERVER_ADDR"):
            http = httpx.AsyncClient(timeout=5, trust_env=False)
            nacos = NacosClient(
                http,
                os.environ["NACOS_SERVER_ADDR"],
                os.environ.get("NACOS_NAMESPACE", "public"),
                os.environ.get("NACOS_GROUP", "X_SENTINEL"),
                os.environ.get("NACOS_USERNAME", ""),
                os.environ.get("NACOS_PASSWORD", ""),
            )
            ip, port = advertise_identity()
            await nacos.register(
                os.environ.get("NACOS_SERVICE_NAME", "xsentinel-auth-center"), ip, port
            )
            beat = asyncio.create_task(
                heartbeat_loop(
                    nacos, os.environ.get("NACOS_SERVICE_NAME", "xsentinel-auth-center"), ip, port
                )
            )
        yield
        if beat:
            beat.cancel()
            with suppress(asyncio.CancelledError):
                await beat
        if nacos:
            await nacos.http.aclose()

    app = FastAPI(
        title="X Sentinel service authentication",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health/live")
    async def live():
        return {"status": "healthy"}

    @app.post("/v1/token")
    async def issue(request: TokenRequest) -> ServiceTokenResponse:
        now = time.time()
        requests = app.state.requests
        while requests and requests[0] < now - 60:
            requests.popleft()
        if len(requests) >= int(os.environ.get("SERVICE_AUTH_RATE_LIMIT", "120")):
            raise HTTPException(429, "Token request limit reached", headers={"Retry-After": "60"})
        requests.append(now)
        client = app.state.clients.get(request.client_id, {})
        digest = hashlib.sha256(request.client_secret.get_secret_value().encode()).hexdigest()
        valid = hmac.compare_digest(digest, client.get("secret_sha256", "0" * 64))
        grants = client.get("grants", {})
        if not valid or request.audience not in grants:
            raise HTTPException(401, "Invalid service credentials or audience")
        lifetime = 120
        token = jwt.encode(
            {
                "sub": request.client_id,
                "aud": request.audience,
                "iss": ISSUER,
                "iat": int(now),
                "nbf": int(now),
                "exp": int(now) + lifetime,
                "type": "service",
                "scope": grants[request.audience],
            },
            app.state.private_key,
            algorithm="RS256",
        )
        return ServiceTokenResponse(access_token=token, expires_in=lifetime)

    return app


app = create_app()
