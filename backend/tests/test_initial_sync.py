from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.api.routes.monitored_users import create_monitored_user, initial_sync_since_id
from app.schemas.monitored_user import MonitoredUserCreate
from app.services.poller import PollClaim, calculate_pagination_state
from app.services.twscrape_client import _is_not_newer
from app.services.x_client import TweetBatch


@pytest.mark.parametrize("days", [0, -1, 1.5, 7.0, True, "7", None, 2147483648])
def test_history_days_reject_invalid_values(days):
    with pytest.raises(ValidationError):
        MonitoredUserCreate(username="openai", initial_sync_days=days)


def test_history_default_and_custom_days():
    assert MonitoredUserCreate(username="openai").initial_sync_days == 7
    assert MonitoredUserCreate(username="openai", initial_sync_days=42).initial_sync_days == 42


def test_cutoff_includes_boundary_and_excludes_older_tweets():
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    cutoff = now - timedelta(days=7)
    boundary_id = (int(cutoff.timestamp() * 1000) - 1288834974657) << 22
    since_id = initial_sync_since_id(now, 7)
    assert _is_not_newer(str(boundary_id - 1), since_id)
    assert not _is_not_newer(str(boundary_id), since_id)
    assert not _is_not_newer(str(boundary_id + (86400000 << 22)), since_id)
    assert initial_sync_since_id(now, 2147483647) == "0"


async def test_create_seeds_durable_history_checkpoint():
    db = AsyncMock()
    added = []
    db.add = added.append
    before = datetime.now(UTC)
    with (
        patch(
            "app.api.routes.monitored_users.get_polling_settings", new=AsyncMock(return_value={})
        ),
        patch("app.api.routes.monitored_users._serialize_user", return_value="serialized"),
    ):
        await create_monitored_user(
            MonitoredUserCreate(username="openai", initial_sync_days=3), db, object()
        )
    after = datetime.now(UTC)
    user = added[0]
    assert user.initial_sync_days == 3
    assert int(initial_sync_since_id(before, 3)) <= int(user.last_tweet_id)
    assert int(user.last_tweet_id) <= int(initial_sync_since_id(after, 3))
    db.commit.assert_awaited_once()


def test_history_checkpoint_survives_empty_results_and_pagination():
    floor = initial_sync_since_id(datetime(2026, 9, 29, tzinfo=UTC), 7)
    claim = PollClaim(
        user_id=1,
        log_id=1,
        generation=1,
        trigger="scheduled",
        manual_token=None,
        username="openai",
        x_user_id="42",
        since_id=floor,
        pagination_token=None,
        pagination_since_id=None,
        pagination_newest_id=None,
        include_replies=True,
        include_retweets=True,
    )
    empty = calculate_pagination_state(
        claim, TweetBatch(tweets=[], newest_id=None, next_token=None, result_count=0)
    )
    assert empty.last_tweet_id == floor
    page = calculate_pagination_state(
        claim,
        TweetBatch(tweets=[], newest_id=str(int(floor) + 100), next_token="page2", result_count=0),
    )
    assert page.since_id == floor
    assert page.last_tweet_id == floor
