import asyncio
import time
from pathlib import Path

import httpx
import jwt
from fastapi import HTTPException, Request

from app.control_plane.contracts import ServiceTokenResponse

ISSUER = "xsentinel-service-auth"


class ServiceVerifier:
    """Only the auth center holds the signing key; services verify locally."""

    def __init__(self, public_key: str, audience: str, scope: str):
        self.public_key = public_key
        self.audience = audience
        self.scope = scope

    async def __call__(self, request: Request) -> dict:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise jwt.InvalidTokenError("Missing bearer token")
            claims = jwt.decode(
                token,
                self.public_key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=ISSUER,
                options={"require": ["sub", "aud", "iss", "iat", "nbf", "exp", "scope", "type"]},
            )
            if claims["type"] != "service" or self.scope not in claims["scope"].split():
                raise jwt.InvalidTokenError("Invalid service scope")
            return claims
        except (jwt.InvalidTokenError, KeyError, TypeError, AttributeError):
            raise HTTPException(
                401,
                "Invalid service token",
                headers={
                    "WWW-Authenticate": "Bearer",
                },
            ) from None


class TokenClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        url: str,
        client_id: str,
        secret_file: str,
        resolver=None,
        nacos=None,
    ):
        self.http = http
        self.url = url.rstrip("/")
        self.client_id = client_id
        self.secret = Path(secret_file).read_text().strip()
        self.cache: dict[str, tuple[str, float]] = {}
        self.lock = asyncio.Lock()
        self.resolver = resolver
        self.nacos = nacos

    async def token(self, audience: str) -> str:
        async with self.lock:
            cached = self.cache.get(audience)
            if cached and cached[1] > time.monotonic():
                return cached[0]
            base_url = self.url
            if self.resolver is not None:
                base_url = (await self.resolver()).url
            elif self.nacos is not None:
                base_url = (await self.nacos.discover("xsentinel-auth-center")).url
            response = await self.http.post(
                f"{base_url}/v1/token",
                json={
                    "client_id": self.client_id,
                    "client_secret": self.secret,
                    "audience": audience,
                },
            )
            response.raise_for_status()
            payload = ServiceTokenResponse.model_validate(response.json())
            token = payload.access_token
            self.cache[audience] = (token, time.monotonic() + payload.expires_in - 15)
            return token
