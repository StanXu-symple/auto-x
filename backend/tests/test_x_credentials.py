import pytest

from app.core.config import Settings
from app.services.x_credentials import (
    XCredentialUnavailableError,
    decrypt_token,
    encrypt_token,
    token_fingerprint,
    token_hint,
)


def test_x_token_round_trip_is_encrypted_and_masked() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key="j" * 64,
        x_token_encryption_key="x" * 64,
    )
    token = "example-secret-bearer-token-1234"
    encrypted = encrypt_token(token, settings)
    assert token not in encrypted
    assert decrypt_token(encrypted, settings) == token
    assert token_hint(token) == "••••••••1234"
    assert len(token_fingerprint(token)) == 64


def test_changed_encryption_key_cannot_decrypt_token() -> None:
    original = Settings(_env_file=None, x_token_encryption_key="a" * 64)
    changed = Settings(_env_file=None, x_token_encryption_key="b" * 64)
    encrypted = encrypt_token("example-secret-bearer-token", original)
    with pytest.raises(XCredentialUnavailableError, match="cannot be decrypted"):
        decrypt_token(encrypted, changed)
