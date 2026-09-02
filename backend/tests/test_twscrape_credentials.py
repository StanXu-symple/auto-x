import pytest

from app.core.config import Settings
from app.services.twscrape_credentials import _parse, _serialize
from app.services.x_credentials import (
    XCredentialUnavailableError,
    decrypt_token,
    encrypt_token,
)


def test_twscrape_cookie_bundle_round_trip_stays_encrypted() -> None:
    settings = Settings(_env_file=None, x_token_encryption_key="t" * 64)
    serialized = _serialize("reader", "auth-secret-value", "csrf-secret-value")
    encrypted = encrypt_token(serialized, settings)

    assert "auth-secret-value" not in encrypted
    assert "csrf-secret-value" not in encrypted
    assert _parse(decrypt_token(encrypted, settings), 3) == {
        "account_label": "reader",
        "auth_token": "auth-secret-value",
        "ct0": "csrf-secret-value",
        "version": 3,
    }


def test_twscrape_cookie_bundle_rejects_incomplete_payload() -> None:
    with pytest.raises(XCredentialUnavailableError, match="incomplete"):
        _parse('{"account_label":"reader","auth_token":"","ct0":"csrf"}', 1)
