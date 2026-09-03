from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from nonebot.adapters.qq import ActionFailed, NetworkError, UnauthorizedException
from nonebot.internal.driver import Response
from pydantic import ValidationError

from app.core.config import Settings
from app.qq_worker import NoneBotQQSender, QQDeliveryClaim, QQDeliveryWorker
from app.schemas.qq import QQTargetCreate, validate_message_template
from app.services.qq_notifications import (
    QQCredentialValidationError,
    decrypt_app_secret,
    encrypt_app_secret,
    render_qq_message,
    secret_fingerprint,
    secret_hint,
    validate_qq_credentials,
)


def qq_settings() -> Settings:
    return Settings(
        _env_file=None,
        jwt_secret_key="j" * 64,
        x_token_encryption_key="q" * 64,
    )


def test_qq_secret_round_trip_is_encrypted_and_masked() -> None:
    settings = qq_settings()
    secret = "official-qq-secret-1234"
    encrypted = encrypt_app_secret(secret, settings)

    assert secret not in encrypted
    assert decrypt_app_secret(encrypted, settings) == secret
    assert secret_hint(secret) == "••••••••1234"
    assert len(secret_fingerprint(secret)) == 64


def test_qq_target_requires_a_subscription_scope() -> None:
    with pytest.raises(ValidationError, match="Select at least one monitored account"):
        QQTargetCreate(
            bot_id=1,
            name="发布群",
            group_openid="group-open-id",
        )

    target = QQTargetCreate(
        bot_id=1,
        name="发布群",
        group_openid="group-open-id",
        monitored_user_ids=[2, 2, 3],
    )
    assert target.monitored_user_ids == [2, 3]


def test_qq_template_rejects_unknown_fields() -> None:
    with pytest.raises(ValueError, match="Unsupported template fields: password"):
        validate_message_template("{username}: {password}")


def test_render_qq_message_includes_tweet_url_and_truncates() -> None:
    tweet = SimpleNamespace(
        text="a" * 4000,
        tweet_id="123456",
        posted_at=datetime(2026, 9, 3, 8, 0, tzinfo=UTC),
    )
    user = SimpleNamespace(username="sentinel", display_name="Sentinel")

    message = render_qq_message(
        "{author} @{username}\n{text}\n{url}\n{posted_at}",
        tweet=tweet,  # type: ignore[arg-type]
        user=user,  # type: ignore[arg-type]
    )

    assert len(message) == 3800
    assert message.startswith("Sentinel @sentinel")
    assert message.endswith("...")


@pytest.mark.asyncio
async def test_validate_qq_credentials_accepts_access_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/app/getAppAccessToken"
        assert request.read() == b'{"appId":"10001","clientSecret":"secret-value"}'
        return httpx.Response(200, json={"access_token": "token", "expires_in": "7200"})

    await validate_qq_credentials(
        "10001",
        "secret-value",
        qq_settings(),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_validate_qq_credentials_classifies_invalid_secret() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(401, json={"message": "invalid app secret"})
    )

    with pytest.raises(QQCredentialValidationError) as error:
        await validate_qq_credentials(
            "10001",
            "bad-secret",
            qq_settings(),
            transport=transport,
        )

    assert error.value.invalid is True


@pytest.mark.asyncio
async def test_nonebot_sender_keeps_multiple_bots_and_replaces_old_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBot:
        def __init__(self, _adapter, _self_id, bot_info) -> None:
            self.bot_info = bot_info

        async def send_to_group(self, **_kwargs):
            return SimpleNamespace(id=f"message-{self.bot_info.id}")

    monkeypatch.setattr("app.qq_worker.Bot", FakeBot)
    sender = NoneBotQQSender(object(), qq_settings())  # type: ignore[arg-type]

    def claim(bot_id: int, version: int) -> QQDeliveryClaim:
        return QQDeliveryClaim(
            delivery_id=bot_id,
            claim_token="claim",
            bot_id=bot_id,
            bot_version=version,
            app_id=str(bot_id),
            app_secret="secret-value",
            group_openid="group-open-id",
            message_body="hello",
            attempts=1,
            max_attempts=3,
        )

    assert await sender.send_group(claim(1, 1)) == "message-1"
    assert await sender.send_group(claim(2, 1)) == "message-2"
    assert set(sender.bots) == {(1, 1), (2, 1)}

    assert await sender.send_group(claim(1, 2)) == "message-1"
    assert set(sender.bots) == {(1, 2), (2, 1)}


def test_qq_worker_retry_classification() -> None:
    assert QQDeliveryWorker._retryable(NetworkError("temporary")) is True
    assert QQDeliveryWorker._retryable(ActionFailed(Response(429))) is True
    assert QQDeliveryWorker._retryable(ActionFailed(Response(503))) is True
    assert QQDeliveryWorker._retryable(ActionFailed(Response(403))) is False
    assert QQDeliveryWorker._retryable(UnauthorizedException(Response(401))) is False
