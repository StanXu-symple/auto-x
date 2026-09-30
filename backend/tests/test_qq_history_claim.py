from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import Settings
from app.models.qq import QQBotAccount, QQDelivery, QQNotificationTarget
from app.models.tweet import Tweet
from app.qq_worker import QQDeliveryWorker


@pytest.mark.parametrize("state", ["queued", "retry_wait", "sending"])
@pytest.mark.parametrize(
    "age,days,cancelled",
    [(4, 3, True), (3, 3, False), (1, 3, False), (4, None, False), (None, 3, True)],
)
async def test_claim_rechecks_history_before_sending(monkeypatch, state, age, days, cancelled):
    created = datetime(2026, 9, 29, 12, tzinfo=UTC)
    delivery = QQDelivery(
        id=1,
        kind="tweet",
        target_id=2,
        source_tweet_id=3,
        status=state,
        next_attempt_at=created,
        lease_expires_at=created,
        attempts=0,
        max_attempts=3,
        group_openid="group",
        message_body="body",
    )
    target = QQNotificationTarget(
        id=2,
        bot_id=4,
        initial_sync_days=days,
        created_at=created,
        is_enabled=True,
        group_openid="group",
    )
    bot = QQBotAccount(
        id=4, app_id="app", version=1, is_enabled=True, encrypted_app_secret="encrypted"
    )
    tweet = None if age is None else Tweet(posted_at=created - timedelta(days=age))
    session = MagicMock()
    session.get = AsyncMock(
        side_effect=lambda model, *_args, **_kw: {
            QQDelivery: delivery,
            QQNotificationTarget: target,
            QQBotAccount: bot,
            Tweet: tweet,
        }[model]
    )
    session.begin.return_value = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr("app.qq_worker.AsyncSessionFactory", lambda: context)
    decrypt = MagicMock(return_value="secret")
    monkeypatch.setattr("app.qq_worker.decrypt_app_secret", decrypt)
    worker = QQDeliveryWorker.__new__(QQDeliveryWorker)
    worker.settings = Settings(_env_file=None, jwt_secret_key="j" * 64)
    worker.worker_id = "test"
    claim = await worker._claim(1, "token")
    if cancelled:
        assert claim is None
        assert delivery.status == "cancelled"
        assert "范围" in delivery.last_error
        assert delivery.attempts == 0
        decrypt.assert_not_called()
    else:
        assert claim is not None
        assert delivery.status == "sending"
