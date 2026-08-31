from app.api.routes.monitored_users import (
    _clear_pagination_checkpoint,
    poll_monitored_user_now,
)
from app.models.monitored_user import MonitoredUser


class FakeDb:
    def __init__(self, user: MonitoredUser) -> None:
        self.user = user
        self.commits = 0

    async def get(self, *_args, **_kwargs):
        return self.user

    async def commit(self):
        self.commits += 1


async def test_manual_poll_uses_replaceable_database_uuid_token() -> None:
    user = MonitoredUser(id=7, username="openai", is_active=True, status="idle")
    db = FakeDb(user)
    await poll_monitored_user_now(7, db, object())  # type: ignore[arg-type]
    first_token = user.manual_poll_token
    await poll_monitored_user_now(7, db, object())  # type: ignore[arg-type]
    assert first_token is not None
    assert user.manual_poll_token is not None
    assert user.manual_poll_token != first_token
    assert user.status == "queued"
    assert db.commits == 2


def test_filter_change_checkpoint_invalidation_clears_all_fields() -> None:
    user = MonitoredUser(
        id=7,
        username="openai",
        pagination_token="page-2",
        pagination_since_id="100",
        pagination_newest_id="200",
    )
    _clear_pagination_checkpoint(user)
    assert user.pagination_token is None
    assert user.pagination_since_id is None
    assert user.pagination_newest_id is None
