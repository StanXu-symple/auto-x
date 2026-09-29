from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.api.errors import APIError
from app.api.routes.qq import push_task_now
from app.models.qq import QQBotAccount, QQScheduledTask


def make_db(*, enabled=True, groups=None, bot_enabled=True):
    task = QQScheduledTask(
        id=9,
        name="Daily",
        message="Hello",
        is_enabled=enabled,
        next_run_at=datetime(2026, 10, 1, tzinfo=UTC),
        last_run_at=None,
    )
    bot = QQBotAccount(id=2, name="Bot", app_id="app", version=1, is_enabled=bot_enabled)
    db = AsyncMock()
    db.get.side_effect = lambda model, *_args, **_kwargs: task if model is QQScheduledTask else bot
    db.scalars.return_value = [2]
    db.execute.return_value = SimpleNamespace(tuples=lambda: groups or [(2, "group-a")])
    rows = []
    db.add_all = rows.extend

    async def flush():
        for index, row in enumerate(rows, 1):
            row.id = index

    db.flush.side_effect = flush
    return db, task, rows


@pytest.mark.parametrize("enabled", [True, False])
async def test_push_records_task_history_before_enqueue_without_changing_schedule(enabled):
    db, task, rows = make_db(
        enabled=enabled, groups=[(2, "group-a"), (2, "group-b"), (2, "group-a"), (3, "other")]
    )
    schedule = task.next_run_at
    redis = Mock()

    async def enqueue(client, ids):
        assert client is redis
        db.commit.assert_awaited_once()
        assert ids == [1, 2]

    with patch("app.api.routes.qq.enqueue_qq_delivery_ids", side_effect=enqueue):
        result = await push_task_now(9, db, redis, object())
    assert result.delivery_ids == [1, 2]
    assert result.batch_count == 2
    assert {row.group_openid for row in rows} == {"group-a", "group-b"}
    assert all(row.task_id == 9 and row.message_body == "Hello" for row in rows)
    assert all(row.kind == "scheduled" and row.status == "queued" for row in rows)
    assert task.next_run_at == schedule
    assert task.last_run_at is None
    assert task.is_enabled == enabled


async def test_missing_task_returns_not_found():
    db = AsyncMock()
    db.get.return_value = None
    with pytest.raises(APIError) as error:
        await push_task_now(99, db, Mock(), object())
    assert error.value.status_code == 404
    db.commit.assert_not_awaited()


async def test_no_enabled_recipients_does_not_report_success():
    db, _, rows = make_db(bot_enabled=False)
    with pytest.raises(APIError) as error:
        await push_task_now(9, db, Mock(), object())
    assert error.value.status_code == 409
    assert rows == []
    db.commit.assert_not_awaited()


async def test_each_manual_push_has_distinct_delivery_keys():
    keys = []
    with patch("app.api.routes.qq.enqueue_qq_delivery_ids", new=AsyncMock()):
        for _ in range(2):
            db, _, rows = make_db()
            await push_task_now(9, db, Mock(), object())
            keys.append(rows[0].idempotency_key)
    assert keys[0] != keys[1]


async def test_failed_commit_never_notifies_worker():
    db, _, _ = make_db()
    db.commit.side_effect = RuntimeError("database failed")
    with patch("app.api.routes.qq.enqueue_qq_delivery_ids", new=AsyncMock()) as enqueue:
        with pytest.raises(RuntimeError):
            await push_task_now(9, db, Mock(), object())
        enqueue.assert_not_awaited()
