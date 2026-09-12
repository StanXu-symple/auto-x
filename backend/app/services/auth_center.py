"""Client and verifier for the shared authentication center.

The backend deliberately never receives a signing key.  Administrator tokens
are issued by ``xsentinel-auth-center`` and verified locally with its JWKS;
service tokens use a different token type, audience, and scope and therefore
cannot be used as administrator credentials.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from typing import Any

import httpx
import jwt

from app.control_plane.nacos import NacosClient
from app.core.config import Settings

AUTH_ISSUER = "xsentinel-auth"
ADMIN_AUDIENCE = "xsentinel-backend"
ADMIN_SCOPE = "admin"
AUTH_CENTER_SERVICE = "xsentinel-auth-center"
JWKS_CACHE_HARD_LIMIT_SECONDS = 60.0
UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS = 5.0
UNKNOWN_KID_CACHE_MAX_ENTRIES = 256


def _jwks_cache_ttl(headers: Mapping[str, str], configured_ttl: float) -> float:
    """Return a bounded JWKS cache lifetime, honoring Cache-Control."""

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


class AuthCenterUnavailable(RuntimeError):
    """The authentication authority could not be reached."""


class AuthCenterRejected(RuntimeError):
    """The authority rejected a proxied administrator request."""

    def __init__(
        self, status_code: int, payload: object, *, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(f"Authentication center returned HTTP {status_code}")
        self.status_code = status_code
        self.payload = payload
        self.headers = dict(headers or {})


class AuthCenterClient:
    """Resolve auth-center, proxy user operations, and cache public JWKs."""

    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        jwks_ttl_seconds: float = 300,
    ) -> None:
        self.settings = settings
        self.http = http or httpx.AsyncClient(
            timeout=settings.monitor_request_timeout_seconds,
            trust_env=False,
        )
        self._owns_http = http is None
        self.nacos = (
            NacosClient(
                self.http,
                settings.nacos_server_addr,
                settings.nacos_namespace,
                settings.nacos_group,
                settings.nacos_username,
                settings.nacos_password,
            )
            if settings.nacos_server_addr
            else None
        )
        self.jwks_ttl_seconds = jwks_ttl_seconds
        self._keys: dict[str, Any] = {}
        self._keys_expire_at = 0.0
        self._jwks_lock = asyncio.Lock()
        self._unknown_kids: dict[str, float] = {}
        self._unknown_refresh_allowed_at = 0.0

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def _base_url(self) -> str:
        if self.settings.service_auth_url:
            return self.settings.service_auth_url.rstrip("/")
        if self.nacos is not None:
            try:
                return (await self.nacos.discover(AUTH_CENTER_SERVICE)).url
            except (httpx.HTTPError, RuntimeError, KeyError, TypeError, ValueError) as exc:
                raise AuthCenterUnavailable("Authentication center discovery failed") from exc
        raise AuthCenterUnavailable("Authentication center is not configured")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        bearer_token: str = "",
        forwarded_for: str = "",
        request_id: str = "",
    ) -> object:
        payload, _headers = await self._request_with_headers(
            method,
            path,
            json_body=json_body,
            bearer_token=bearer_token,
            forwarded_for=forwarded_for,
            request_id=request_id,
        )
        return payload

    async def _request_with_headers(
        self,
        method: str,
        path: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        bearer_token: str = "",
        forwarded_for: str = "",
        request_id: str = "",
    ) -> tuple[object, Mapping[str, str]]:
        headers: dict[str, str] = {}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        if forwarded_for:
            headers["X-Forwarded-For"] = forwarded_for
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            response = await self.http.request(
                method,
                f"{await self._base_url()}{path}",
                json=dict(json_body) if json_body is not None else None,
                headers=headers,
            )
        except (httpx.HTTPError, AuthCenterUnavailable) as exc:
            if isinstance(exc, AuthCenterUnavailable):
                raise
            raise AuthCenterUnavailable("Authentication center request failed") from exc

        try:
            payload: object = response.json()
        except ValueError:
            payload = {"detail": "Authentication center returned an invalid response"}
        if response.status_code >= 500:
            raise AuthCenterUnavailable(
                f"Authentication center returned HTTP {response.status_code}"
            )
        if response.is_error:
            forwarded_headers = {
                name: response.headers[name]
                for name in ("retry-after", "www-authenticate")
                if name in response.headers
            }
            raise AuthCenterRejected(
                response.status_code, payload, headers=forwarded_headers
            )
        return payload, response.headers

    async def login(
        self,
        username: str,
        password: str,
        *,
        forwarded_for: str = "",
        request_id: str = "",
    ) -> object:
        return await self._request(
            "POST",
            "/v1/admin/login",
            json_body={"username": username, "password": password},
            forwarded_for=forwarded_for,
            request_id=request_id,
        )

    async def me(self, token: str, *, request_id: str = "") -> object:
        return await self._request(
            "GET", "/v1/admin/me", bearer_token=token, request_id=request_id
        )

    async def change_password(
        self, token: str, payload: Mapping[str, Any], *, request_id: str = ""
    ) -> object:
        return await self._request(
            "PATCH",
            "/v1/admin/password",
            json_body=payload,
            bearer_token=token,
            request_id=request_id,
        )

    async def logout(self, token: str, *, request_id: str = "") -> object:
        return await self._request(
            "POST", "/v1/admin/logout", bearer_token=token, request_id=request_id
        )

    async def _refresh_jwks(
        self, *, force: bool = False, unknown_kid: str | None = None
    ) -> bool:
        async with self._jwks_lock:
            now = time.monotonic()
            if unknown_kid is not None:
                negative_until = self._unknown_kids.get(unknown_kid, 0.0)
                if now < negative_until or now < self._unknown_refresh_allowed_at:
                    return False
                # Advance the cooldown before I/O so an unavailable authority
                # cannot be hammered by a stream of distinct unknown key ids.
                self._unknown_refresh_allowed_at = (
                    now + UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
                )
            if not force and self._keys and now < self._keys_expire_at:
                return False
            payload, headers = await self._request_with_headers(
                "GET", "/.well-known/jwks.json"
            )
            if not isinstance(payload, Mapping) or not isinstance(payload.get("keys"), list):
                raise AuthCenterUnavailable("Authentication center returned an invalid JWKS")
            keys: dict[str, Any] = {}
            try:
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
            except (jwt.PyJWKError, KeyError, TypeError, ValueError) as exc:
                raise AuthCenterUnavailable(
                    "Authentication center returned an invalid JWKS"
                ) from exc
            if not keys:
                raise AuthCenterUnavailable("Authentication center returned no signing keys")
            self._keys = keys
            refreshed_at = time.monotonic()
            ttl = _jwks_cache_ttl(headers, self.jwks_ttl_seconds)
            self._keys_expire_at = refreshed_at + ttl
            self._unknown_kids = {
                value: expires_at
                for value, expires_at in self._unknown_kids.items()
                if expires_at > refreshed_at and value not in keys
            }
            if unknown_kid is not None:
                cooldown_until = (
                    refreshed_at + UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS
                )
                self._unknown_refresh_allowed_at = cooldown_until
                if unknown_kid not in keys:
                    self._remember_unknown_kid(unknown_kid)
            return True

    def _remember_unknown_kid(self, kid: str) -> None:
        now = time.monotonic()
        self._unknown_kids = {
            value: expires_at
            for value, expires_at in self._unknown_kids.items()
            if expires_at > now
        }
        cache_full = len(self._unknown_kids) >= UNKNOWN_KID_CACHE_MAX_ENTRIES
        expires_at = min(
            now + UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS,
            self._keys_expire_at,
        )
        if expires_at <= now:
            return
        if kid not in self._unknown_kids and cache_full:
            self._unknown_kids.pop(next(iter(self._unknown_kids)))
        self._unknown_kids[kid] = expires_at

    @staticmethod
    def _validate_claim_shape(claims: dict[str, Any]) -> None:
        uid = claims.get("uid")
        sid = claims.get("sid")
        jti = claims.get("jti")
        scope = claims.get("scope")
        if isinstance(uid, bool) or not isinstance(uid, int) or uid <= 0:
            raise jwt.InvalidTokenError("Invalid administrator id")
        if not isinstance(sid, str) or not sid or len(sid) > 128:
            raise jwt.InvalidTokenError("Invalid session id")
        if not isinstance(jti, str) or not jti or len(jti) > 128:
            raise jwt.InvalidTokenError("Invalid token id")
        if not isinstance(scope, str) or ADMIN_SCOPE not in scope.split():
            raise jwt.InvalidTokenError("Missing administrator scope")
        if claims.get("type") != "user":
            raise jwt.InvalidTokenError("Unexpected token type")

    async def verify_user_token(self, token: str) -> dict[str, Any]:
        """Verify a user JWT, refreshing JWKS once for rotation or a new key."""

        try:
            header = jwt.get_unverified_header(token)
        except (jwt.InvalidTokenError, TypeError, ValueError):
            raise jwt.InvalidTokenError("Malformed token header") from None
        kid = header.get("kid")
        if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
            raise jwt.InvalidTokenError("Unsupported signing key")

        refreshed = await self._refresh_jwks()
        key = self._keys.get(kid)
        if key is None and not refreshed:
            await self._refresh_jwks(force=True, unknown_kid=kid)
            key = self._keys.get(kid)
        if key is None:
            if refreshed:
                self._unknown_refresh_allowed_at = max(
                    self._unknown_refresh_allowed_at,
                    time.monotonic() + UNKNOWN_KID_REFRESH_COOLDOWN_SECONDS,
                )
            self._remember_unknown_kid(kid)
            raise jwt.InvalidTokenError("Unknown signing key")

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=ADMIN_AUDIENCE,
            issuer=AUTH_ISSUER,
            options={
                "require": [
                    "sub",
                    "uid",
                    "sid",
                    "jti",
                    "kid",
                    "aud",
                    "iss",
                    "iat",
                    "nbf",
                    "exp",
                    "type",
                    "scope",
                ]
            },
            leeway=5,
        )
        if claims.get("kid") != kid:
            raise jwt.InvalidTokenError("Signing key id mismatch")
        self._validate_claim_shape(claims)
        return claims
