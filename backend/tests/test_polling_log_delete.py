import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import APIError
from app.api.routes.polling_logs import clear_polling_logs, delete_polling_log
from app.db.base import Base
from app.models.monitored_user import MonitoredUser
from app.models.polling_log import PollingLog
from app.services.poller import PollingService


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda connection: Base.metadata.create_all(
                connection, tables=[MonitoredUser.__table__, PollingLog.__table__]
            )
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all(
            [MonitoredUser(id=1, username="alice"), MonitoredUser(id=2, username="bob")]
        )
        session.add_all(
            [
                PollingLog(id=i, monitored_user_id=(i % 2) + 1, status=status)
                for i, status in enumerate(["success", "running", "failed"], 1)
            ]
        )
        await session.commit()
        yield session
    await engine.dispose()


async def test_delete_only_selected_record_and_keep_accounts(db):
    await delete_polling_log(1, db, object())
    assert list(await db.scalars(select(PollingLog.id).order_by(PollingLog.id))) == [2, 3]
    assert len(list(await db.scalars(select(MonitoredUser)))) == 2
    with pytest.raises(APIError) as error:
        await delete_polling_log(1, db, object())
    assert error.value.status_code == 404


async def test_clear_all_accounts_and_statuses_is_repeatable(db):
    await clear_polling_logs(db, object())
    assert list(await db.scalars(select(PollingLog.id))) == []
    assert len(list(await db.scalars(select(MonitoredUser)))) == 2
    await clear_polling_logs(db, object())


async def test_deleted_running_log_is_not_recreated_by_worker(db):
    await delete_polling_log(2, db, object())
    await PollingService._finalize_log(db, 2, status="success", started_perf=time.perf_counter())
    await db.commit()
    assert await db.get(PollingLog, 2) is None


async def test_finalization_locks_log_until_worker_transaction_commits():
    db = AsyncMock()
    db.get.return_value = None
    await PollingService._finalize_log(db, 1, status="success", started_perf=time.perf_counter())
    db.get.assert_awaited_once_with(PollingLog, 1, with_for_update=True)
