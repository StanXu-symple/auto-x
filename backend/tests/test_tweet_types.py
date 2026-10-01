from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.models.qq import QQNotificationTarget
from app.models.qq_placeholder import QQPlaceholder
from app.schemas.qq import QQTargetCreate, QQTargetUpdate
from app.schemas.qq_placeholder import DEFAULT_PLACEHOLDERS
from app.services.poller import PollingService
from app.services.qq_notifications import create_target_history_deliveries, create_tweet_deliveries
from app.services.tweet_types import classify_tweet


@pytest.mark.parametrize(
    "references,expected",
    [
        (None, "original"),
        ([], "original"),
        ([{"type": "replied_to"}], "reply"),
        ([{"type": "retweeted"}], "retweet"),
        ([{"type": "quoted"}], "retweet"),
        ([{"type": "replied_to"}, {"type": "quoted"}], "reply"),
        ([{"type": "replied_to"}, {"type": "retweeted"}], "retweet"),
    ],
)
def test_ingestion_stores_normalized_type_without_changing_references(references, expected):
    payload = {"id": "123", "created_at": "2026-09-30T01:00:00Z", "referenced_tweets": references}
    assert classify_tweet(payload) == expected
    values = PollingService._tweet_values(1, "42", payload, datetime.now(UTC))
    assert values["tweet_type"] == expected
    assert values["referenced_tweets"] == references
    assert values["raw_payload"] == payload


@pytest.mark.parametrize("mode", [None, "unknown", "", 1])
def test_invalid_modes_are_rejected(mode):
    with pytest.raises(ValidationError):
        QQTargetCreate(
            bot_id=1, name="群", group_openid="abc", all_monitored_users=True, listen_mode=mode
        )
    with pytest.raises(ValidationError):
        QQTargetUpdate(listen_mode=mode)


def test_legacy_targets_default_to_all_and_patch_omission_preserves_mode():
    assert (
        QQTargetCreate(
            bot_id=1, name="群", group_openid="abc", all_monitored_users=True
        ).listen_mode
        == "all"
    )
    assert "listen_mode" not in QQTargetUpdate(is_enabled=False).model_dump(exclude_unset=True)


@pytest.mark.parametrize(
    "mode,expected", [("all", [1, 2, 3]), ("original", [1]), ("reply", [2]), ("retweet", [3])]
)
@pytest.mark.parametrize("only_target_id", [None, 5])
async def test_live_and_backfill_delivery_filtering(mode, expected, only_target_id):
    now = datetime.now(UTC)
    target = QQNotificationTarget(
        id=5,
        listen_mode=mode,
        initial_sync_days=3,
        created_at=now,
        all_monitored_users=True,
        name="群",
        group_openid="abc",
        message_template="{text}",
        template_variables={},
    )
    tweets = [
        SimpleNamespace(id=i, monitored_user_id=1, posted_at=now, tweet_type=kind)
        for i, kind in enumerate(["original", "reply", "retweet"], 1)
    ]
    db = AsyncMock()
    db.scalars.side_effect = [
        tweets,
        [SimpleNamespace(id=1)],
        [],
        [QQPlaceholder(**row) for row in DEFAULT_PLACEHOLDERS],
    ]
    db.execute.side_effect = [
        SimpleNamespace(
            all=lambda: [(target, SimpleNamespace(name="bot", app_id="app", version=1))]
        ),
        SimpleNamespace(tuples=lambda: []),
    ]
    added = []
    db.add_all = added.extend
    with patch("app.services.qq_notifications.render_qq_message", return_value="message"):
        await create_tweet_deliveries(
            db, ["1", "2", "3"], max_attempts=3, only_target_id=only_target_id
        )
    assert [row.source_tweet_id for row in added] == expected


@pytest.mark.parametrize("mode", ["original", "reply", "retweet"])
async def test_history_query_filters_type_before_loading_batches(mode):
    target = QQNotificationTarget(
        id=1,
        listen_mode=mode,
        initial_sync_days=3,
        created_at=datetime.now(UTC),
        is_enabled=True,
        all_monitored_users=True,
    )
    db = AsyncMock()
    db.scalars.return_value = []
    await create_target_history_deliveries(db, target, max_attempts=3)
    query = db.scalars.call_args.args[0]
    assert "tweets.tweet_type =" in str(query)
    assert query.compile().params["tweet_type_1"] == mode


async def test_target_mode_create_update_and_response_roundtrip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.routes.qq import create_target, update_target
    from app.db.base import Base
    from app.models.monitored_user import MonitoredUser
    from app.models.qq import QQBotAccount, QQTargetSubscription

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda connection: Base.metadata.create_all(
                connection,
                tables=[
                    MonitoredUser.__table__,
                    QQBotAccount.__table__,
                    QQNotificationTarget.__table__,
                    QQTargetSubscription.__table__,
                    QQPlaceholder.__table__,
                ],
            )
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(
            QQBotAccount(
                id=1,
                name="机器人",
                app_id="app",
                encrypted_app_secret="secret",
                secret_hint="hint",
                secret_fingerprint="fingerprint",
            )
        )
        await db.commit()
        payload = QQTargetCreate(
            bot_id=1,
            name="群",
            group_openid="abc",
            all_monitored_users=True,
            message_template="内容",
            listen_mode="reply",
        )
        with patch(
            "app.api.routes.qq.create_target_history_deliveries", new=AsyncMock(return_value=[])
        ) as history:
            result = await create_target(payload, db, AsyncMock(), object())
        assert result.listen_mode == "reply"
        assert history.call_args.args[1].listen_mode == "reply"
        result = await update_target(
            result.id, QQTargetUpdate(listen_mode="original"), db, object()
        )
        assert result.listen_mode == "original"
        result = await update_target(result.id, QQTargetUpdate(is_enabled=False), db, object())
        assert result.listen_mode == "original"
        assert (await db.get(QQNotificationTarget, result.id)).listen_mode == "original"
    await engine.dispose()


async def test_content_stream_filters_type_and_exposes_it_in_detail():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.api.routes.tweets import get_tweet, list_tweets
    from app.db.base import Base
    from app.models.monitored_user import MonitoredUser
    from app.models.tweet import Tweet
    from app.models.tweet_screenshot import TweetScreenshot

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda connection: Base.metadata.create_all(
                connection,
                tables=[MonitoredUser.__table__, Tweet.__table__, TweetScreenshot.__table__],
            )
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        db.add(
            MonitoredUser(
                id=1,
                username="alice",
                display_name="Alice",
                include_replies=True,
                include_retweets=True,
            )
        )
        now = datetime.now(UTC)
        for i, kind in enumerate(["original", "reply", "retweet"], 1):
            db.add(
                Tweet(
                    id=i,
                    tweet_id=str(i),
                    monitored_user_id=1,
                    author_id="42",
                    text="内容",
                    tweet_type=kind,
                    posted_at=now,
                    raw_payload={},
                )
            )
        await db.commit()
        result = await list_tweets(
            db, object(), page=1, page_size=15, username=None, search=None, tweet_type="reply"
        )
        assert result.total == 1
        assert [(item.tweet_id, item.tweet_type, item.display_name) for item in result.items] == [
            ("2", "reply", "Alice")
        ]
        detail = await get_tweet("3", db, object())
        assert detail.tweet_type == "retweet"
    await engine.dispose()
