from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_current_admin, get_redis
from app.api.routes.qq import router as qq_router
from app.api.routes.qq_placeholders import router as placeholder_router
from app.db.base import Base
from app.db.session import get_db
from app.models.qq import (
    QQBotAccount,
    QQDelivery,
    QQNotificationTarget,
    QQScheduledTask,
    QQScheduledTaskBot,
    QQScheduledTaskGroup,
    QQTargetSubscription,
)
from app.models.qq_placeholder import QQPlaceholder


@pytest.fixture
async def client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        model.__table__
        for model in (
            QQBotAccount,
            QQNotificationTarget,
            QQTargetSubscription,
            QQScheduledTask,
            QQScheduledTaskBot,
            QQScheduledTaskGroup,
            QQPlaceholder,
            QQDelivery,
        )
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda connection: Base.metadata.create_all(connection, tables=tables))
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        now = datetime(2026, 9, 30, tzinfo=UTC)
        for i in range(1, 33):
            db.add(
                QQBotAccount(
                    id=i,
                    name=f"机器人 {i}",
                    app_id=str(i),
                    encrypted_app_secret="secret",
                    secret_hint="hint",
                    secret_fingerprint=str(i),
                    created_at=now,
                )
            )
            db.add(
                QQNotificationTarget(
                    id=i,
                    bot_id=i,
                    name=f"目标 {i}",
                    group_openid=f"group-{i}",
                    message_template="{text}",
                    created_at=now,
                )
            )
            db.add(
                QQScheduledTask(
                    id=i,
                    name=f"任务 {i}",
                    message="消息",
                    frequency="daily",
                    run_time="09:00:00",
                    is_enabled=i % 2 == 0,
                    next_run_at=now,
                    created_at=now,
                )
            )
            db.add(QQScheduledTaskBot(task_id=i, bot_id=i))
            db.add(QQScheduledTaskGroup(task_id=i, bot_id=i, group_openid=f"group-{i}"))
            db.add(QQPlaceholder(id=i, placeholder=f"{{field_{i}}}", source_field="text"))
            db.add(
                QQDelivery(
                    id=i,
                    task_id=1 if i <= 30 else 2,
                    idempotency_key=str(i),
                    bot_name="机器人",
                    bot_app_id="1",
                    bot_version=1,
                    target_name="目标",
                    group_openid="group",
                    message_body="消息",
                    created_at=now,
                )
            )
        await db.commit()
        app = FastAPI()
        app.include_router(qq_router)
        app.include_router(placeholder_router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_admin] = lambda: object()
        app.dependency_overrides[get_redis] = lambda: AsyncMock(get=AsyncMock(return_value=None))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            yield http
    await engine.dispose()


@pytest.mark.parametrize("path", ["bots", "targets", "tasks", "placeholders", "deliveries"])
async def test_pages_are_bounded_complete_and_stably_ordered(client, path):
    pages = []
    for page, expected_size in [(1, 15), (2, 15), (3, 2), (4, 0)]:
        response = await client.get(f"/qq/{path}", params={"page": page, "page_size": 15})
        assert response.status_code == 200, response.text
        result = response.json()
        assert (result["total"], result["page"], result["page_size"]) == (32, page, 15)
        assert len(result["items"]) == expected_size
        pages.extend(int(row["id"]) for row in result["items"])
        if path == "tasks":
            assert result["enabled_total"] == 16
            for row in result["items"]:
                assert row["bot_ids"] == [int(row["id"])]
                assert row["groups"] == [
                    {"bot_id": int(row["id"]), "group_openid": f"group-{row['id']}"}
                ]
    assert pages == list(range(1, 33) if path == "placeholders" else range(32, 0, -1))
    resized = (await client.get(f"/qq/{path}?page=2&page_size=30")).json()
    assert len(resized["items"]) == 2


@pytest.mark.parametrize("path", ["bots", "targets", "tasks", "placeholders"])
async def test_unpaginated_callers_keep_full_selection_lists(client, path):
    response = await client.get(f"/qq/{path}")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
    assert len(response.json()) == 32


@pytest.mark.parametrize(
    "params",
    [{"page": 0}, {"page": -1}, {"page": 1, "page_size": 0}, {"page": 1, "page_size": 101}],
)
@pytest.mark.parametrize("path", ["bots", "targets", "tasks", "placeholders"])
async def test_pagination_rejects_invalid_bounds(client, path, params):
    response = await client.get(f"/qq/{path}", params=params)
    assert response.status_code == 422


async def test_task_history_filters_before_counting_and_paginating(client):
    result = (await client.get("/qq/deliveries?task_id=1&page=2&page_size=15")).json()
    assert result["total"] == 30
    assert [int(row["id"]) for row in result["items"]] == list(range(15, 0, -1))
