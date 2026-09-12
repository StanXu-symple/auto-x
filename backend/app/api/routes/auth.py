from __future__ import annotations

import ipaddress
from collections.abc import Mapping

from fastapi import APIRouter, Request

from app.api.deps import CurrentAdmin
from app.api.errors import APIError
from app.schemas.auth import AdminPublic, ChangePasswordRequest, LoginRequest, TokenResponse
from app.schemas.common import MessageResponse
from app.services.auth_center import (
    AuthCenterClient,
    AuthCenterRejected,
    AuthCenterUnavailable,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

_TRUSTED_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
    )
)


def _client(request: Request) -> AuthCenterClient:
    client = getattr(request.app.state, "auth_center", None)
    if not isinstance(client, AuthCenterClient):
        raise APIError(503, "auth_unavailable", "Authentication service is unavailable")
    return client


def _token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise APIError(401, "not_authenticated", "Authentication credentials were not provided")
    return token


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))[:128]


def _forwarded_for(request: Request) -> str:
    peer = request.client.host if request.client else ""
    try:
        peer_address = ipaddress.ip_address(peer)
    except ValueError:
        peer_address = None
    trusted_peer = peer_address is not None and any(
        peer_address in network for network in _TRUSTED_PROXY_NETWORKS
    )
    forwarded_values = request.headers.getlist("x-forwarded-for")
    if trusted_peer and len(forwarded_values) == 1:
        # The edge nginx replaces, rather than appends to, this header. Reject
        # chains and duplicate fields so an incorrectly configured proxy cannot
        # silently reintroduce a caller-controlled left-most address.
        forwarded = forwarded_values[0].strip()
        try:
            if forwarded and "," not in forwarded and len(forwarded) <= 64:
                return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    # A direct public caller must not choose its auth-center rate-limit bucket
    # by supplying a forged X-Forwarded-For header.
    return peer[:64]


def _raise_auth_error(exc: AuthCenterRejected, default_code: str) -> None:
    code = default_code
    message = "Authentication request was rejected"
    details: object = None
    if isinstance(exc.payload, Mapping):
        error = exc.payload.get("error")
        if isinstance(error, Mapping):
            remote_code = error.get("code")
            remote_message = error.get("message")
            if isinstance(remote_code, str) and remote_code:
                code = remote_code
            if isinstance(remote_message, str) and remote_message:
                message = remote_message
            details = error.get("details")
        else:
            detail = exc.payload.get("detail")
            if isinstance(detail, str) and detail:
                message = detail
            elif detail is not None:
                details = detail
    raise APIError(
        exc.status_code,
        code,
        message,
        details=details,
        headers=exc.headers or None,
    )


async def _proxy(operation, *, default_code: str):
    try:
        return await operation
    except AuthCenterUnavailable:
        raise APIError(503, "auth_unavailable", "Authentication service is unavailable") from None
    except AuthCenterRejected as exc:
        _raise_auth_error(exc, default_code)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request) -> TokenResponse:
    result = await _proxy(
        _client(request).login(
            payload.username,
            payload.password,
            forwarded_for=_forwarded_for(request),
            request_id=_request_id(request),
        ),
        default_code="invalid_credentials",
    )
    try:
        return TokenResponse.model_validate(result)
    except (TypeError, ValueError):
        raise APIError(
            502,
            "auth_invalid_response",
            "Authentication service returned an invalid response",
        ) from None


@router.get("/me", response_model=AdminPublic)
async def current_admin(request: Request, _: CurrentAdmin) -> AdminPublic:
    result = await _proxy(
        _client(request).me(_token(request), request_id=_request_id(request)),
        default_code="invalid_token",
    )
    try:
        return AdminPublic.model_validate(result)
    except (TypeError, ValueError):
        raise APIError(
            502,
            "auth_invalid_response",
            "Authentication service returned an invalid response",
        ) from None


@router.patch("/password", response_model=MessageResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    _: CurrentAdmin,
) -> MessageResponse:
    result = await _proxy(
        _client(request).change_password(
            _token(request),
            payload.model_dump(),
            request_id=_request_id(request),
        ),
        default_code="invalid_token",
    )
    try:
        return MessageResponse.model_validate(result)
    except (TypeError, ValueError):
        raise APIError(
            502,
            "auth_invalid_response",
            "Authentication service returned an invalid response",
        ) from None


@router.post("/logout", response_model=MessageResponse)
async def logout(request: Request, _: CurrentAdmin) -> MessageResponse:
    result = await _proxy(
        _client(request).logout(_token(request), request_id=_request_id(request)),
        default_code="invalid_token",
    )
    try:
        return MessageResponse.model_validate(result)
    except (TypeError, ValueError):
        raise APIError(
            502,
            "auth_invalid_response",
            "Authentication service returned an invalid response",
        ) from None
