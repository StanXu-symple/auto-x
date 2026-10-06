from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.api.errors import APIError
from app.api.routes.qq import clear_deliveries, clear_task_history, delete_delivery


async def test_delete_delivery_limits_deletion_to_selected_id():
    db = AsyncMock()
    row = SimpleNamespace(status="sent", article_publish_attempt_id=None)
    db.get.return_value = row
    await delete_delivery(42, db, object())
    assert db.get.call_args.args[1] == 42
    db.delete.assert_awaited_once_with(row)
    db.commit.assert_awaited_once()


async def test_delete_missing_delivery_returns_404():
    db = AsyncMock()
    db.get.return_value = None
    with pytest.raises(APIError) as error:
        await delete_delivery(42, db, object())
    assert error.value.status_code == 404
    db.commit.assert_not_awaited()


async def test_clear_deletes_all_rows_without_page_or_status_filter():
    db = AsyncMock()
    db.scalar.side_effect = [None, None]
    await clear_deliveries(db, object())
    statement = db.execute.call_args.args[0]
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert compiled.startswith("DELETE FROM qq_deliveries WHERE")
    assert "ai_publish_dispatches" in compiled
    assert "qq_deliveries.status" in compiled
    db.commit.assert_awaited_once()


async def test_clear_task_history_preserves_sending_delivery():
    db = AsyncMock()
    db.get.return_value = object()
    db.scalar.return_value = 17
    with pytest.raises(APIError) as error:
        await clear_task_history(2, db, object())
    assert error.value.status_code == 409
    db.execute.assert_not_awaited()
    db.commit.assert_not_awaited()
