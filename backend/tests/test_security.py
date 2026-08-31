from datetime import timedelta

import pytest
from jwt import ExpiredSignatureError

from app.core.config import Settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_and_verify() -> None:
    digest = hash_password("correct horse battery staple")
    assert digest != "correct horse battery staple"
    assert verify_password("correct horse battery staple", digest)
    assert not verify_password("wrong", digest)


def test_access_token_round_trip() -> None:
    settings = Settings(
        _env_file=None,
        app_name="X Sentinel Test",
        jwt_secret_key="a-test-secret-that-is-long-enough-for-tests",
    )
    token, expires_in = create_access_token("admin", user_id=7, settings=settings)
    payload = decode_access_token(token, settings=settings)
    assert expires_in == settings.jwt_expire_minutes * 60
    assert payload["sub"] == "admin"
    assert payload["uid"] == 7


def test_expired_access_token_is_rejected() -> None:
    settings = Settings(
        _env_file=None,
        app_name="X Sentinel Test",
        jwt_secret_key="a-test-secret-that-is-long-enough-for-tests",
    )
    token, _ = create_access_token(
        "admin",
        user_id=7,
        settings=settings,
        expires_delta=timedelta(seconds=-1),
    )
    with pytest.raises(ExpiredSignatureError):
        decode_access_token(token, settings=settings)
