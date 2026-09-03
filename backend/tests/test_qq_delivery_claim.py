from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.qq_worker import QQDeliveryWorker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "bot_state", "joined", "expected_error"),
    [
        ("batch", "enabled", True, None),
        ("batch", "disabled", True, "机器人已删除或停用"),
        ("batch", "deleted", True, "机器人已删除或停用"),
        ("batch", "changed", True, "机器人凭据已变更"),
        ("batch", "enabled", False, "机器人已退出目标群"),
        ("tweet", "enabled", True, "机器人或群通知目标已删除或停用"),
        ("test", "enabled", True, None),
    ],
)
async def test_claim_handles_batches_without_subscription_target(
    monkeypatch, kind, bot_state, joined, expected_error,
):
    delivery = SimpleNamespace(
        id=10, kind=kind, target_id=20 if kind == "test" else None,
        bot_app_id="10001", bot_version=1, group_openid="selected-group",
        status="queued", next_attempt_at=datetime.now(UTC), attempts=0,
        max_attempts=3, message_body="merged content", last_error="previous error",
    )
    bot = None if bot_state == "deleted" else SimpleNamespace(
        id=1, app_id="10001", version=2 if bot_state == "changed" else 1,
        is_enabled=bot_state != "disabled", encrypted_app_secret="encrypted",
    )
    target = SimpleNamespace(bot_id=1, is_enabled=True, group_openid="selected-group")
    session = MagicMock()
    session.get = AsyncMock(side_effect=[delivery, target, bot] if kind == "test" else [delivery])
    session.scalar = AsyncMock(side_effect=[bot, 1 if joined else None])
    session.begin.return_value = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr("app.qq_worker.AsyncSessionFactory", lambda: context)
    monkeypatch.setattr("app.qq_worker.decrypt_app_secret", lambda *_: "secret")
    worker = QQDeliveryWorker.__new__(QQDeliveryWorker)
    worker.settings = Settings(_env_file=None, jwt_secret_key="j" * 64)
    worker.worker_id = "test-worker"

    claim = await worker._claim(delivery.id, "claim-token")

    if expected_error:
        assert claim is None
        assert delivery.status == "cancelled"
        assert expected_error in delivery.last_error
        assert delivery.attempts == 0
    else:
        assert claim is not None
        assert claim.app_id == "10001"
        assert claim.group_openid == "selected-group"
        assert claim.message_body == "merged content"
        assert delivery.status == "sending"
        assert delivery.attempts == 1
        assert delivery.last_error is None
    if kind == "batch":
        # A null target ID must never be looked up for a manual batch.
        assert session.get.await_count == 1
