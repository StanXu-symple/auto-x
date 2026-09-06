import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from nonebot.adapters.qq import ActionFailed, NetworkError, UnauthorizedException
from nonebot.internal.driver import Response
from pydantic import ValidationError

from app.core.config import Settings
from app.qq_worker import (
    NoneBotQQSender,
    QQDeliveryClaim,
    QQDeliveryWorker,
    QQFailureCommitResult,
)
from app.schemas.qq import QQTargetCreate, validate_message_template
from app.services.qq_notifications import (
    QQCredentialValidationError,
    decrypt_app_secret,
    encrypt_app_secret,
    render_qq_message,
    secret_fingerprint,
    secret_hint,
    split_qq_text,
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


def test_split_qq_text_preserves_all_content_without_oversized_chunks() -> None:
    text = "第一段\n" + ("长" * 25) + "\n最后一段"

    chunks = split_qq_text(text, max_chars=10)

    assert all(len(chunk) <= 10 for chunk in chunks)
    assert "".join(chunks) == text


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

    assert len(message) == 2000
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


@pytest.mark.asyncio
async def test_nonebot_sender_sends_article_image_as_local_attachment(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    image = tmp_path / "article.png"
    image.write_bytes(b"image-bytes")
    sent = {}

    class FakeBot:
        def __init__(self, *_args) -> None:
            pass

        async def send_to_group(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(id="image-message")

    monkeypatch.setattr("app.qq_worker.Bot", FakeBot)
    monkeypatch.setattr("app.qq_worker.article_delivery_media_path", lambda _path: image)
    sender = NoneBotQQSender(object(), qq_settings())  # type: ignore[arg-type]
    claim = QQDeliveryClaim(
        delivery_id=1,
        claim_token="claim",
        bot_id=1,
        bot_version=1,
        app_id="1",
        app_secret="secret-value",
        group_openid="group-open-id",
        message_body="",
        attempts=1,
        max_attempts=3,
        media_path=str(image),
    )

    assert await sender.send_group(claim) == "image-message"
    assert sent["message"].type == "file_image"
    assert sent["message"].data["content"] == b"image-bytes"


def test_qq_worker_retry_classification() -> None:
    assert QQDeliveryWorker._retryable(NetworkError("temporary")) is True
    assert QQDeliveryWorker._retryable(ActionFailed(Response(429))) is True
    assert QQDeliveryWorker._retryable(ActionFailed(Response(503))) is True
    assert QQDeliveryWorker._retryable(ActionFailed(Response(403))) is False
    assert QQDeliveryWorker._retryable(UnauthorizedException(Response(401))) is False


class WorkerRedis:
    async def set(self, *_args, **_kwargs):
        return True

    async def get(self, *_args):
        return None

    async def eval(self, *_args):
        return 1


def delivery_claim() -> QQDeliveryClaim:
    return QQDeliveryClaim(
        delivery_id=11,
        claim_token="claim-token",
        bot_id=2,
        bot_version=1,
        app_id="app-id",
        app_secret="SECRET QQ APP SECRET",
        group_openid="SECRET GROUP OPENID",
        message_body="SECRET MESSAGE BODY",
        attempts=1,
        max_attempts=3,
        kind="article",
        article_id=7,
        sequence=2,
    )


def delivery_worker(sender) -> QQDeliveryWorker:
    worker = QQDeliveryWorker.__new__(QQDeliveryWorker)
    worker.settings = Settings(
        _env_file=None,
        qq_worker_lock_ttl_seconds=60,
        qq_worker_send_interval_seconds=0,
    )
    worker.sender = sender
    worker.worker_id = "qq-worker-test"
    worker.redis = WorkerRedis()  # type: ignore[assignment]
    worker.stop_event = asyncio.Event()
    return worker


@pytest.mark.asyncio
async def test_qq_delivery_logs_ordered_safe_success_stages(caplog) -> None:
    class Sender:
        async def send_group(self, _claim):
            return "message-id"

    worker = delivery_worker(Sender())
    claim = delivery_claim()

    async def claim_delivery(*_args):
        return claim

    async def commit_success(*_args):
        return True

    worker._claim = claim_delivery  # type: ignore[method-assign]
    worker._commit_success = commit_success  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="app.qq_worker"):
        assert await worker.process_delivery(claim.delivery_id) is True

    stages = [record.stage for record in caplog.records if hasattr(record, "stage")]
    assert stages == [
        "delivery_discovered",
        "send_gate_wait_started",
        "send_gate_acquired",
        "delivery_lock_acquired",
        "delivery_claim_started",
        "delivery_claimed",
        "qq_api_send_started",
        "qq_api_send_completed",
        "delivery_persistence_started",
        "delivery_completed",
        "send_gate_released",
    ]
    completed = next(record for record in caplog.records if record.stage == "delivery_completed")
    assert completed.delivery_id == 11
    assert completed.worker_id == "qq-worker-test"
    assert completed.delivery_kind == "article"
    assert completed.delivery_type == "text"
    assert completed.article_id == 7
    assert completed.sequence == 2
    assert completed.attempt == 1
    assert completed.max_attempts == 3
    assert completed.message_character_count == len(claim.message_body)
    log_values = repr([record.__dict__ for record in caplog.records])
    assert "SECRET QQ APP SECRET" not in log_values
    assert "SECRET GROUP OPENID" not in log_values
    assert "SECRET MESSAGE BODY" not in log_values


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_stage", "retryable", "retry_delay"),
    [
        ("retry_wait", "delivery_retry_scheduled", True, 10),
        ("failed", "delivery_failed_permanently", False, None),
    ],
)
async def test_qq_delivery_logs_retry_or_permanent_failure(
    caplog,
    outcome: str,
    expected_stage: str,
    retryable: bool,
    retry_delay: int | None,
) -> None:
    class Sender:
        async def send_group(self, _claim):
            if retryable:
                raise NetworkError("QQ transport failed: SECRET RESPONSE")
            raise RuntimeError("QQ rejected request: SECRET RESPONSE")

    worker = delivery_worker(Sender())
    claim = delivery_claim()

    async def claim_delivery(*_args):
        return claim

    async def commit_failure(*_args, **_kwargs):
        return QQFailureCommitResult(
            outcome=outcome,
            attempt=1,
            max_attempts=3,
            retry_delay_seconds=retry_delay,
            next_attempt_at=(datetime.now(UTC) + timedelta(seconds=10))
            if retry_delay
            else None,
        )

    worker._claim = claim_delivery  # type: ignore[method-assign]
    worker._commit_failure = commit_failure  # type: ignore[method-assign]

    with caplog.at_level(logging.INFO, logger="app.qq_worker"):
        assert await worker.process_delivery(claim.delivery_id) is False

    failure = next(record for record in caplog.records if record.stage == expected_stage)
    assert failure.outcome == outcome
    assert failure.retryable is retryable
    assert failure.retry_delay_seconds == retry_delay
    assert failure.error_summary.endswith(
        "QQ transport failed" if retryable else "QQ rejected request"
    )
    assert "SECRET RESPONSE" not in caplog.text


@pytest.mark.asyncio
async def test_slow_qq_send_logs_progress_and_cleans_up(monkeypatch, caplog) -> None:
    finished = asyncio.Event()
    sender_cancelled = asyncio.Event()

    class Sender:
        async def send_group(self, _claim):
            try:
                await finished.wait()
                return "message-id"
            except asyncio.CancelledError:
                sender_cancelled.set()
                raise

    worker = delivery_worker(Sender())
    monkeypatch.setattr("app.qq_worker.QQ_SEND_PROGRESS_INTERVAL_SECONDS", 0.01)
    context = {
        "delivery_id": 11,
        "attempt": 1,
        "max_attempts": 3,
        "delivery_type": "text",
    }

    with caplog.at_level(logging.INFO, logger="app.qq_worker"):
        task = asyncio.create_task(
            worker._send_with_progress(
                delivery_claim(),
                started_perf=asyncio.get_running_loop().time(),
                log_context=context,
            )
        )
        await asyncio.sleep(0.025)
        finished.set()
        assert await task == "message-id"

    progress = [
        record for record in caplog.records if record.stage == "qq_api_send_in_progress"
    ]
    assert progress
    assert progress[0].send_elapsed_ms >= 0
    assert not sender_cancelled.is_set()
