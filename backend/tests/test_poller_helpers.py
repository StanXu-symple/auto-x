import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects import mysql

from app.core.config import Settings
from app.core.time import as_utc
from app.models.monitored_user import MonitoredUser
from app.models.polling_log import PollingLog
from app.models.setting import AppSetting
from app.services.poller import (
    LockLostError,
    PollClaim,
    PollingService,
    calculate_pagination_state,
    parse_x_datetime,
)
from app.services.settings_service import effective_interval
from app.services.x_client import TweetBatch


def test_effective_poll_interval_prefers_user_override() -> None:
    values = {"global_poll_interval_seconds": 300, "max_concurrency": 5}
    assert effective_interval(None, values) == 300
    assert effective_interval(45, values) == 45


def test_tweet_values_preserve_full_payload() -> None:
    now = datetime.now(UTC)
    payload = {
        "id": "123",
        "author_id": "42",
        "text": "hello",
        "created_at": "2026-01-01T12:00:00.000Z",
        "public_metrics": {
            "like_count": 5,
            "retweet_count": 4,
            "reply_count": 3,
            "quote_count": 2,
            "bookmark_count": 1,
            "impression_count": 100,
        },
        "entities": {"hashtags": []},
        "attachments": {"media_keys": ["m1"]},
        "referenced_tweets": [{"type": "quoted", "id": "100"}],
        "_includes": {"media": [{"media_key": "m1", "url": "https://example.test/x"}]},
    }
    values = PollingService._tweet_values(8, "42", payload, now)
    assert values["monitored_user_id"] == 8
    assert values["like_count"] == 5
    assert values["raw_payload"]["_includes"]["media"][0]["media_key"] == "m1"
    assert values["posted_at"] == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_invalid_x_datetime_falls_back_to_utc_now() -> None:
    parsed = parse_x_datetime("not-a-date")
    assert parsed.tzinfo == UTC


@pytest.mark.asyncio
async def test_distributed_lock_prevents_duplicate_poll() -> None:
    class LockedRedis:
        async def set(self, *args, **kwargs):
            return False

    class SessionFactoryThatMustNotRun:
        def __call__(self):
            raise AssertionError("database must not be touched when the lock is held")

    service = PollingService(
        session_factory=SessionFactoryThatMustNotRun(),  # type: ignore[arg-type]
        redis=LockedRedis(),  # type: ignore[arg-type]
        x_client=object(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        worker_id="test-worker",
    )
    assert await service.poll_user(1) is False


def make_claim(**overrides) -> PollClaim:
    values = {
        "user_id": 1,
        "log_id": 1,
        "generation": 1,
        "trigger": "scheduled",
        "manual_token": None,
        "username": "openai",
        "x_user_id": "42",
        "since_id": "100",
        "pagination_token": None,
        "pagination_since_id": None,
        "pagination_newest_id": None,
        "include_replies": True,
        "include_retweets": True,
    }
    values.update(overrides)
    return PollClaim(**values)


def test_checkpoint_does_not_advance_high_water_until_drained() -> None:
    first = calculate_pagination_state(
        make_claim(),
        TweetBatch(tweets=[], newest_id="200", next_token="page-2", result_count=100),
    )
    assert first.last_tweet_id == "100"
    assert first.token == "page-2"
    assert first.since_id == "100"
    assert first.newest_id == "200"
    assert first.status == "backfilling"

    drained = calculate_pagination_state(
        make_claim(
            pagination_token="page-2",
            pagination_since_id="100",
            pagination_newest_id="200",
        ),
        TweetBatch(tweets=[], newest_id="150", next_token=None, result_count=20),
    )
    assert drained.last_tweet_id == "200"
    assert drained.token is None
    assert drained.since_id is None
    assert drained.status == "idle"


@pytest.mark.asyncio
async def test_tweet_upsert_is_chunked_and_never_uses_insert_ignore() -> None:
    class FakeSession:
        def __init__(self):
            self.statements = []

        async def scalars(self, _statement):
            return []

        async def execute(self, statement):
            self.statements.append(statement)

    session = FakeSession()
    service = PollingService(
        session_factory=object(),  # type: ignore[arg-type]
        redis=object(),  # type: ignore[arg-type]
        x_client=object(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        worker_id="test",
    )
    rows = [{"tweet_id": str(index)} for index in range(301)]
    inserted, new_ids = await service._upsert_tweets(session, rows)  # type: ignore[arg-type]
    assert inserted == 301
    assert new_ids == [str(index) for index in range(301)]
    assert len(session.statements) == 3
    sql = str(session.statements[0].compile(dialect=mysql.dialect())).upper()
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "INSERT IGNORE" not in sql


@pytest.mark.asyncio
async def test_superseded_generation_cannot_advance_cursor_or_insert() -> None:
    user = MonitoredUser(
        id=1,
        username="openai",
        poll_generation=2,
        last_tweet_id="100",
    )
    log = PollingLog(id=9, monitored_user_id=1, trigger="scheduled", status="running")
    polling = AppSetting(
        key="polling",
        value={"global_poll_interval_seconds": 300, "max_concurrency": 5},
    )

    class Context:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *_args):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return Context(None)

        async def scalar(self, _statement):
            return polling

        async def get(self, entity, _identity, **_kwargs):
            if entity is MonitoredUser:
                return user
            if entity is PollingLog:
                return log
            return None

    class Redis:
        async def eval(self, *_args):
            return 1

    service = PollingService(
        session_factory=lambda: Session(),  # type: ignore[arg-type]
        redis=Redis(),  # type: ignore[arg-type]
        x_client=object(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        worker_id="test",
    )
    result = await service._commit_success(
        make_claim(generation=1, log_id=9),
        x_user_id="42",
        resolved_user=None,
        batch=TweetBatch(tweets=[], newest_id="200", next_token=None, result_count=0),
        started_perf=0.0,
        lock_key="lock",
        lock_token="token",
        lost_lock=asyncio.Event(),
    )
    assert result.status == "superseded"
    assert not result.applied
    assert user.last_tweet_id == "100"
    assert log.status == "superseded"


@pytest.mark.asyncio
async def test_lock_verification_fails_closed() -> None:
    class Redis:
        async def eval(self, *_args):
            return 0

    lost = asyncio.Event()
    service = PollingService(
        session_factory=object(),  # type: ignore[arg-type]
        redis=Redis(),  # type: ignore[arg-type]
        x_client=object(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        worker_id="test",
    )
    with pytest.raises(LockLostError):
        await service._assert_lock("lock", "token", lost)
    assert lost.is_set()


@pytest.mark.asyncio
async def test_new_manual_token_is_not_overwritten_by_older_poll() -> None:
    user = MonitoredUser(
        id=1,
        username="openai",
        x_user_id="42",
        is_active=True,
        include_replies=True,
        include_retweets=True,
        poll_interval_seconds=None,
        status="polling",
        last_tweet_id="100",
        pagination_token=None,
        pagination_since_id=None,
        pagination_newest_id=None,
        manual_poll_token="new-request",
        poll_generation=1,
        last_error=None,
        consecutive_failures=0,
        next_poll_at=datetime.now(UTC),
    )
    log = PollingLog(id=10, monitored_user_id=1, trigger="manual", status="running")
    polling = AppSetting(
        key="polling",
        value={"global_poll_interval_seconds": 300, "max_concurrency": 5},
    )

    class Context:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *_args):
            return None

    class Session:
        def __init__(self):
            self.scalar_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def begin(self):
            return Context()

        async def scalar(self, _statement):
            self.scalar_calls += 1
            return polling if self.scalar_calls == 1 else None

        async def get(self, entity, _identity, **_kwargs):
            if entity is MonitoredUser:
                return user
            if entity is PollingLog:
                return log
            return None

    class Redis:
        async def eval(self, *_args):
            return 1

    session = Session()
    service = PollingService(
        session_factory=lambda: session,  # type: ignore[arg-type]
        redis=Redis(),  # type: ignore[arg-type]
        x_client=object(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
        worker_id="test",
    )
    result = await service._commit_success(
        make_claim(generation=1, log_id=10, trigger="manual", manual_token="old-request"),
        x_user_id="42",
        resolved_user=None,
        batch=TweetBatch(tweets=[], newest_id="200", next_token=None, result_count=0),
        started_perf=0.0,
        lock_key="lock",
        lock_token="token",
        lost_lock=asyncio.Event(),
    )
    assert result.applied
    assert user.last_tweet_id == "200"
    assert user.manual_poll_token == "new-request"
    assert user.status == "queued"
    assert as_utc(user.next_poll_at) <= datetime.now(UTC)
