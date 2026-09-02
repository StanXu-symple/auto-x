import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.ai_data_source import AIDataSourceSave
from app.services.x_credentials import decrypt_token, encrypt_token


def test_ai_api_key_uses_encrypted_storage_round_trip() -> None:
    settings = Settings(_env_file=None, x_token_encryption_key="a" * 64)
    api_key = "sk-example-super-secret"
    encrypted = encrypt_token(api_key, settings)

    assert api_key not in encrypted
    assert decrypt_token(encrypted, settings) == api_key


def test_ai_data_source_accepts_openai_and_local_compatible_urls() -> None:
    official = AIDataSourceSave(
        name="OpenAI",
        base_url="https://api.openai.com/v1/",
        model="gpt-5.6-terra",
        api_key="sk-example-value",
    )
    local = AIDataSourceSave(
        name="Local gateway",
        base_url="http://127.0.0.1:8080/v1",
        model="gpt-compatible",
        api_key="local-example-key",
    )

    assert official.base_url == "https://api.openai.com/v1"
    assert local.protocol == "openai_responses"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://remote.invalid/v1",
        "https://token@example.com/v1",
        "file:///tmp/provider",
    ],
)
def test_ai_data_source_rejects_unsafe_urls(base_url: str) -> None:
    with pytest.raises(ValidationError):
        AIDataSourceSave(
            name="Unsafe",
            base_url=base_url,
            model="gpt-test",
            api_key="sk-example-value",
        )
