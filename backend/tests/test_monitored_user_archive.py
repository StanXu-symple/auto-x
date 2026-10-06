from datetime import UTC, datetime

from app.api.routes.monitored_users import delete_monitored_user
from app.models.monitored_user import MonitoredUser


class FakeDb:
    def __init__(self, user: MonitoredUser, *, has_content: bool, has_task: bool) -> None:
        self.results = iter([user, 10 if has_content else None, 20 if has_task else None])
        self.deleted: list[MonitoredUser] = []
        self.commits = 0

    async def scalar(self, _statement):
        return next(self.results)

    async def delete(self, user: MonitoredUser) -> None:
        self.deleted.append(user)

    async def commit(self) -> None:
        self.commits += 1


async def test_delete_preserves_existing_content_and_stops_polling() -> None:
    user = MonitoredUser(
        id=7,
        username="openai",
        is_active=True,
        status="queued",
        next_poll_at=datetime.now(UTC),
        manual_poll_token="claim",
        poll_generation=4,
    )
    db = FakeDb(user, has_content=True, has_task=False)

    result = await delete_monitored_user(7, db, object())  # type: ignore[arg-type]

    assert user.archived_at is not None
    assert user.is_active is False
    assert user.status == "archived"
    assert user.next_poll_at is None
    assert user.manual_poll_token is None
    assert user.poll_generation == 5
    assert db.deleted == []
    assert db.commits == 1
    assert "已归档" in result.message


async def test_delete_preserves_task_subscription_without_content() -> None:
    user = MonitoredUser(id=8, username="anthropicai", is_active=False, poll_generation=0)
    db = FakeDb(user, has_content=False, has_task=True)

    result = await delete_monitored_user(8, db, object())  # type: ignore[arg-type]

    assert user.archived_at is not None
    assert db.deleted == []
    assert "已归档" in result.message


async def test_delete_empty_unreferenced_account_removes_it() -> None:
    user = MonitoredUser(id=9, username="example", is_active=False)
    db = FakeDb(user, has_content=False, has_task=False)

    result = await delete_monitored_user(9, db, object())  # type: ignore[arg-type]

    assert db.deleted == [user]
    assert db.commits == 1
    assert "已删除" in result.message
