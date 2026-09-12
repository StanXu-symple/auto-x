import asyncio
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

from app.control_plane.contracts import ServiceTokenResponse

ISSUER = "xsentinel-auth"
JWKS_CACHE_HARD_LIMIT_SECONDS = 60.0
UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 5.0
UNKNOWN_KID_CACHE_MAX_ENTRIES = 256


def _jwks_cache_ttl(headers: Mapping[str, str], configured_ttl: float) -> float:
    ttl = min(max(0.0, configured_ttl), JWKS_CACHE_HARD_LIMIT_SECONDS)
    directives = headers.get("cache-control", "").split(",")
    for directive in directives:
        name, separator, value = directive.strip().partition("=")
        if name.lower() in {"no-cache", "no-store"}:
            return 0.0
        if name.lower() != "max-age" or not separator:
            continue
        try:
            max_age = max(0.0, float(value.strip().strip('"')))
        except ValueError:
            continue
        ttl = min(ttl, max_age)
    return ttl


class ServiceVerifier:
    """Verify service JWTs with a rotating auth-center JWKS.

    ``public_key`` remains available for standalone/legacy deployments, but a
    resolver, Nacos client, or explicit auth-center URL enables key rotation
    without copying private or newly generated public key files to every node.
    """

    def __init__(
        self,
        public_key: str | None,
        audience: str,
        scope: str,
        *,
        http: httpx.AsyncClient | None = None,
        auth_center_url: str = "",
        resolver=None,
        nacos=None,
        jwks_ttl_seconds: float = 300,
    ):
        self.public_key = public_key
        self.audience = audience
        self.scope = scope
        self.http = http
        self.auth_center_url = auth_center_url.rstrip("/")
        self.resolver = resolver
        self.nacos = nacos
        self.jwks_ttl_seconds = jwks_ttl_seconds
        self.keys: dict[str, Any] = {}
        self.keys_expire_at = 0.0
        self.lock = asyncio.Lock()
        self.unknown_kids: dict[str, float] = {}
        self.unknown_refresh_allowed_at = 0.0

    @property
    def dynamic(self) -> bool:
        return bool(
            self.http is not None
            and (self.auth_center_url or self.resolver is not None or self.nacos is not None)
        )

    async def _base_url(self) -> str:
        if self.auth_center_url:
            return self.auth_center_url
        if self.resolver is not None:
            resolved = await self.resolver()
            return str(getattr(resolved, "url", resolved)).rstrip("/")
        if self.nacos is not None:
            return (await self.nacos.discover("xsentinel-auth-center")).url
        raise RuntimeError("Authentication center is not configured")

    async def _refresh_keys(
        self, *, force: bool = False, unknown_kid: str | None = None
    ) -> bool:
        if not self.dynamic or self.http is None:
            return False
        async with self.lock:
            now = time.monotonic()
            if unknown_kid is not None:
                negative_until = self.unknown_kids.get(unknown_kid, 0.0)
                if now < negative_until or now < self.unknown_refresh_allowed_at:
                    return False
                # Advance the cooldown before I/O so an unavailable authority
                # cannot be hammered by a stream of distinct unknown key ids.
                self.unknown_refresh_allowed_at = (
                    now + UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
                )
            if not force and self.keys and now < self.keys_expire_at:
                return False
            response = await self.http.get(f"{await self._base_url()}/.well-known/jwks.json")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping) or not isinstance(payload.get("keys"), list):
                raise ValueError("Invalid authentication JWKS")
            keys: dict[str, Any] = {}
            for value in payload["keys"]:
                if not isinstance(value, Mapping):
                    continue
                kid = value.get("kid")
                if (
                    isinstance(kid, str)
                    and kid
                    and value.get("kty") == "RSA"
                    and value.get("alg", "RS256") == "RS256"
                    and value.get("use", "sig") == "sig"
                ):
                    keys[kid] = jwt.PyJWK.from_dict(dict(value)).key
            if not keys:
                raise ValueError("Authentication JWKS has no signing keys")
            self.keys = keys
            refreshed_at = time.monotonic()
            ttl = _jwks_cache_ttl(response.headers, self.jwks_ttl_seconds)
            self.keys_expire_at = refreshed_at + ttl
            self.unknown_kids = {
                value: expires_at
                for value, expires_at in self.unknown_kids.items()
                if expires_at > refreshed_at and value not in keys
            }
            if unknown_kid is not None:
                cooldown_until = (
                    refreshed_at + UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
                )
                self.unknown_refresh_allowed_at = cooldown_until
                if unknown_kid not in keys:
                    self._remember_unknown_kid(unknown_kid)
            return True

    def _remember_unknown_kid(self, kid: str) -> None:
        now = time.monotonic()
        self.unknown_kids = {
            value: expires_at
            for value, expires_at in self.unknown_kids.items()
            if expires_at > now
        }
        expires_at = min(
            now + UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS,
            self.keys_expire_at,
        )
        if expires_at <= now:
            return
        if kid not in self.unknown_kids and len(self.unknown_kids) >= UNKNOWN_KID_CACHE_MAX_ENTRIES:
            self.unknown_kids.pop(next(iter(self.unknown_kids)))
        self.unknown_kids[kid] = expires_at

    async def _decode(self, token: str) -> dict[str, Any]:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
            raise jwt.InvalidTokenError("Unsupported signing key")

        if self.dynamic:
            refreshed = await self._refresh_keys()
            key = self.keys.get(kid)
            if key is None and not refreshed:
                await self._refresh_keys(force=True, unknown_kid=kid)
                key = self.keys.get(kid)
            if key is None:
                if refreshed:
                    self.unknown_refresh_allowed_at = max(
                        self.unknown_refresh_allowed_at,
                        time.monotonic() + UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS,
                    )
                self._remember_unknown_kid(kid)
                raise jwt.InvalidTokenError("Unknown signing key")
        else:
            if not self.public_key:
                raise RuntimeError("No service-auth verification key is configured")
            key = self.public_key

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=ISSUER,
            options={
                "require": [
                    "sub",
                    "aud",
                    "iss",
                    "iat",
                    "nbf",
                    "exp",
                    "jti",
                    "kid",
                    "scope",
                    "type",
                ]
            },
            leeway=5,
        )
        scope = claims.get("scope")
        if (
            claims.get("kid") != kid
            or claims.get("type") != "service"
            or not isinstance(scope, str)
            or self.scope not in scope.split()
        ):
            raise jwt.InvalidTokenError("Invalid service token claims")
        return claims

    async def __call__(self, request: Request) -> dict:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise jwt.InvalidTokenError("Missing bearer token")
            return await self._decode(token)
        except (httpx.HTTPError, jwt.PyJWKError, RuntimeError, ValueError):
            raise HTTPException(503, "Authentication service unavailable") from None
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
            elif not base_url and self.nacos is not None:
                base_url = (await self.nacos.discover("xsentinel-auth-center")).url
            if not base_url:
                raise RuntimeError("Authentication center is not configured")
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
