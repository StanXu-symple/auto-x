from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.api.errors import APIError
from app.api.routes.qq import clear_deliveries, delete_delivery


async def test_delete_delivery_limits_deletion_to_selected_id():
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: 42)
    await delete_delivery(42, db, object())
    statement = db.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "WHERE qq_deliveries.id =" in str(compiled)
    assert compiled.params == {"id_1": 42}
    db.commit.assert_awaited_once()


async def test_delete_missing_delivery_returns_404():
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    with pytest.raises(APIError) as error:
        await delete_delivery(42, db, object())
    assert error.value.status_code == 404
    db.commit.assert_not_awaited()


async def test_clear_deletes_all_rows_without_page_or_status_filter():
    db = AsyncMock()
    await clear_deliveries(db, object())
    statement = db.execute.call_args.args[0]
    assert str(statement.compile(dialect=postgresql.dialect())) == "DELETE FROM qq_deliveries"
    db.commit.assert_awaited_once()
