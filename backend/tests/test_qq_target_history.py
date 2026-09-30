from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.models.qq import QQNotificationTarget
from app.models.qq_placeholder import QQPlaceholder
from app.schemas.qq import QQTargetCreate
from app.schemas.qq_placeholder import DEFAULT_PLACEHOLDERS
from app.services.qq_notifications import (
    create_target_history_deliveries,
    create_tweet_deliveries,
    target_history_cutoff,
)


@pytest.mark.parametrize("days", [None, 0, -1, 1.5, 7.0, "7", True, 2147483648])
def test_invalid_history_days(days):
    with pytest.raises(ValidationError):
        QQTargetCreate(
            bot_id=1,
            name="group",
            group_openid="abc",
            all_monitored_users=True,
            initial_sync_days=days,
        )


def test_default_and_large_history_window():
    assert (
        QQTargetCreate(
            bot_id=1, name="group", group_openid="abc", all_monitored_users=True
        ).initial_sync_days
        == 7
    )
    target = QQNotificationTarget(initial_sync_days=2147483647, created_at=datetime.now(UTC))
    assert target_history_cutoff(target) == datetime.min.replace(tzinfo=UTC)
    target.initial_sync_days = None
    assert target_history_cutoff(target) is None


@pytest.mark.parametrize("days,expected", [(7, [2, 3]), (None, [1, 2, 3])])
async def test_late_ingestion_filters_by_publication_time_and_includes_boundary(days, expected):
    now = datetime(2026, 9, 29, tzinfo=UTC)
    target = QQNotificationTarget(
        id=5,
        initial_sync_days=days,
        created_at=now,
        all_monitored_users=True,
        name="group",
        group_openid="abc",
        message_template="{text}",
        template_variables={},
    )
    bot = SimpleNamespace(name="bot", app_id="app", version=1)
    tweets = [
        SimpleNamespace(id=i, monitored_user_id=1, posted_at=now - timedelta(days=age))
        for i, age in [(1, 8), (2, 7), (3, 0)]
    ]
    db = AsyncMock()
    db.scalars.side_effect = [
        tweets,
        [SimpleNamespace(id=1)],
        [],
        [QQPlaceholder(**row) for row in DEFAULT_PLACEHOLDERS],
    ]
    db.execute.side_effect = [
        SimpleNamespace(all=lambda: [(target, bot)]),
        SimpleNamespace(tuples=lambda: []),
    ]
    added = []
    db.add_all = added.extend
    with patch("app.services.qq_notifications.render_qq_message", return_value="message"):
        await create_tweet_deliveries(db, ["1", "2", "3"], max_attempts=3)
    assert [row.source_tweet_id for row in added] == expected
    assert all(row.target_id == 5 for row in added)


async def test_backfill_scopes_queries_and_batches_for_only_new_target():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    target = QQNotificationTarget(
        id=5, initial_sync_days=3, created_at=now, is_enabled=True, all_monitored_users=False
    )
    db = AsyncMock()
    db.scalars.side_effect = [[SimpleNamespace(id=10, tweet_id="100")], []]
    with patch(
        "app.services.qq_notifications.create_tweet_deliveries", new=AsyncMock(return_value=[42])
    ) as create:
        assert await create_target_history_deliveries(db, target, max_attempts=3) == [42]
        create.assert_awaited_once_with(db, ["100"], max_attempts=3, only_target_id=5)
    query = db.scalars.call_args_list[0].args[0]
    sql = str(query)
    assert "tweets.posted_at >=" in sql
    assert "qq_target_subscriptions.target_id =" in sql
    assert query.compile().params["posted_at_1"] == now - timedelta(days=3)
    assert 200 in query.compile().params.values()


async def test_create_target_persists_history_before_enqueue():
    from app.api.routes.qq import create_target

    db = AsyncMock()
    rows = []
    db.scalars.return_value = [QQPlaceholder(**row) for row in DEFAULT_PLACEHOLDERS]
    db.add = rows.append
    payload = QQTargetCreate(
        bot_id=1, name="group", group_openid="abc", all_monitored_users=True, initial_sync_days=3
    )

    async def notify(*_args):
        db.commit.assert_awaited_once()

    with (
        patch("app.api.routes.qq._get_bot", new=AsyncMock()),
        patch("app.api.routes.qq._replace_subscriptions", new=AsyncMock()),
        patch(
            "app.api.routes.qq.create_target_history_deliveries", new=AsyncMock(return_value=[42])
        ) as history,
        patch("app.api.routes.qq.enqueue_qq_delivery_ids", side_effect=notify),
        patch("app.api.routes.qq._target_out", new=AsyncMock(return_value="ok")),
    ):
        assert await create_target(payload, db, AsyncMock(), object()) == "ok"
        assert rows[0].initial_sync_days == 3
        assert history.call_args.args[1] is rows[0]
