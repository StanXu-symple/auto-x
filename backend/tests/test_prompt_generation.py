"""Prompt generation boundary, durable worker and provider protocol tests."""

import asyncio
import importlib.util
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import func, inspect, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_current_admin, get_db
from app.api.errors import APIError
from app.api.routes import prompt_generation
from app.core.config import Settings
from app.db.base import Base, utcnow
from app.models import Admin, AIDataSource, AISetting, AISkill, PromptGeneration
from app.services import prompt_generation_worker
from app.services.ai_data_source import AIDataSourceRuntime
from app.services.ai_provider import AIProviderClient
from app.services.prompt_generation_worker import PromptGenerationWorker
from app.skills.routes import router as skills_router

RUNTIME = AIDataSourceRuntime(
    "test", "openai_responses", "https://example.test/v1", "test-model", "secret-key", 1
)
RESULT = {
    "name": "文章审核",
    "description": "评估发布价值",
    "instructions": "按提供的标准评估文章。",
}


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db, db.begin():
        db.add_all(
            [
                Admin(id=1, username="test", password_hash="unused"),
                Admin(id=2, username="other", password_hash="unused"),
                AISetting(id=1, enabled=True, request_timeout_seconds=180),
                AIDataSource(
                    id=1,
                    name=RUNTIME.name,
                    protocol=RUNTIME.protocol,
                    base_url=RUNTIME.base_url,
                    model_name=RUNTIME.model,
                    encrypted_api_key="unused",
                    key_hint="test",
                    key_fingerprint="test",
                ),
            ]
        )
    yield factory
    await engine.dispose()


@pytest.fixture
async def api(factory):
    app = FastAPI()
    app.include_router(prompt_generation.router)
    app.include_router(skills_router)

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=1)

    @app.exception_handler(APIError)
    async def error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, app=app)


async def submit(api, **kwargs):
    body = {
        "request_id": str(uuid4()),
        "requirement": "帮我设计文章审核与价值评分的提示词",
        **kwargs,
    }
    response = await api.client.post("/ai/prompt-generations", json=body)
    assert response.status_code == 202, response.text
    return response.json(), body


@pytest.fixture
async def worker(factory, monkeypatch):
    requests = []
    replies = []

    async def model(request):
        requests.append(json.loads(request.content))
        if replies:
            return replies.pop(0)
        return httpx.Response(200, json={"status": "completed", "output_text": json.dumps(RESULT)})

    settings = Settings(_env_file=None, nacos_config_enabled=False)
    async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as http:
        provider = AIProviderClient(settings, http)
        monkeypatch.setattr(
            prompt_generation_worker, "get_ai_data_source", AsyncMock(return_value=RUNTIME)
        )
        instance = PromptGenerationWorker(
            settings, None, provider, factory, asyncio.Semaphore(2), asyncio.Event()
        )
        yield SimpleNamespace(instance=instance, requests=requests, replies=replies)


async def test_generate_preview_then_save_through_existing_prompt_management(api, factory, worker):
    job, body = await submit(api)
    assert job["status"] == "queued" and job["result"] is None
    assert worker.requests == []
    await worker.instance.run_once()
    result = (await api.client.get(f"/ai/prompt-generations/{job['id']}")).json()
    assert result["status"] == "succeeded" and result["result"] == RESULT
    assert "config_snapshot" not in result and "secret-key" not in json.dumps(result)
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AISkill)) == 0
    request = worker.requests[0]
    assert request["model"] == RUNTIME.model
    assert (
        json.loads(request["input"][0]["content"][0]["text"])["requirement"] == body["requirement"]
    )
    assert body["requirement"] not in request["instructions"]
    assert request["text"]["format"]["schema"]["required"] == [
        "name",
        "description",
        "instructions",
    ]
    assert request["store"] is False
    saved = await api.client.post("/ai/skills", json={**result["result"], "content_type": "prompt"})
    assert saved.status_code == 201, saved.text
    assert saved.json()["content_type"] == "prompt"
    assert saved.json()["source_type"] == "custom"
    await worker.instance.process(job["id"])
    assert len(worker.requests) == 1


async def test_request_id_deduplicates_retries_and_rejects_changed_input(api):
    job, body = await submit(api)
    duplicate = await api.client.post("/ai/prompt-generations", json=body)
    assert duplicate.json()["id"] == job["id"]
    conflict = await api.client.post(
        "/ai/prompt-generations", json={**body, "requirement": "不同需求"}
    )
    assert conflict.status_code == 409


@pytest.mark.parametrize("requirement", ["", " \n\t ", "x" * 8001])
async def test_invalid_requirements_are_rejected(api, requirement):
    response = await api.client.post(
        "/ai/prompt-generations", json={"request_id": str(uuid4()), "requirement": requirement}
    )
    assert response.status_code == 422


async def test_disabled_ai_and_missing_source_rejected_before_enqueue(api, factory):
    async with factory() as db, db.begin():
        await db.execute(update(AISetting).values(enabled=False))
    response = await api.client.post(
        "/ai/prompt-generations", json={"request_id": str(uuid4()), "requirement": "审核"}
    )
    assert response.status_code == 409
    async with factory() as db, db.begin():
        await db.execute(update(AISetting).values(enabled=True))
        await db.delete(await db.get(AIDataSource, 1))
    response = await api.client.post(
        "/ai/prompt-generations", json={"request_id": str(uuid4()), "requirement": "审核"}
    )
    assert response.status_code == 409


async def test_authentication_and_owner_isolation(api, worker):
    job, body = await submit(api)
    api.app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=2)
    for method, suffix in (("GET", ""), ("POST", "/cancel")):
        response = await api.client.request(method, f"/ai/prompt-generations/{job['id']}{suffix}")
        assert response.status_code == 404
    api.app.dependency_overrides.pop(get_current_admin)
    assert (await api.client.post("/ai/prompt-generations", json=body)).status_code == 401
    assert (await api.client.get(f"/ai/prompt-generations/{job['id']}")).status_code == 401
    assert worker.requests == []


async def test_cancelled_job_is_not_executed(api, worker):
    job, _ = await submit(api)
    cancelled = await api.client.post(f"/ai/prompt-generations/{job['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    await worker.instance.run_once()
    assert worker.requests == []


async def test_cancel_running_request_interrupts_model_and_discards_late_result(api, worker):
    job, _ = await submit(api)
    started, stopped = asyncio.Event(), asyncio.Event()

    async def pending(**_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    worker.instance.provider.generate_prompt = pending
    task = asyncio.create_task(worker.instance.process(job["id"]))
    await asyncio.wait_for(started.wait(), 2)
    await api.client.post(f"/ai/prompt-generations/{job['id']}/cancel")
    await asyncio.wait_for(task, 3)
    assert stopped.is_set()
    result = (await api.client.get(f"/ai/prompt-generations/{job['id']}")).json()
    assert result["status"] == "cancelled" and result["result"] is None
    assert worker.instance.active_tasks == 0


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(401, text="secret-key: private upstream response"),
        httpx.Response(200, json={"status": "incomplete", "output_text": json.dumps(RESULT)}),
        httpx.Response(200, json={"output_text": "not json"}),
        httpx.Response(200, json={"output_text": json.dumps({**RESULT, "instructions": "  "})}),
        httpx.Response(
            200, json={"output_text": json.dumps({**RESULT, "instructions": "x" * 20001})}
        ),
    ],
)
async def test_invalid_model_outputs_fail_safely(api, worker, reply):
    job, _ = await submit(api)
    worker.replies.append(reply)
    await worker.instance.run_once()
    result = (await api.client.get(f"/ai/prompt-generations/{job['id']}")).json()
    assert result["status"] == "failed" and result["result"] is None
    assert result["error"] and "secret-key" not in result["error"]


async def test_changed_source_never_sends_new_key_to_old_endpoint(api, worker, monkeypatch):
    job, _ = await submit(api)
    monkeypatch.setattr(
        prompt_generation_worker,
        "get_ai_data_source",
        AsyncMock(
            return_value=replace(RUNTIME, base_url="https://changed.test/v1", api_key="new-secret")
        ),
    )
    await worker.instance.run_once()
    result = (await api.client.get(f"/ai/prompt-generations/{job['id']}")).json()
    assert result["status"] == "failed"
    assert worker.requests == []


async def test_expired_lease_cannot_publish_and_is_recovered_as_failure(api, worker, factory):
    job, _ = await submit(api)
    async with factory() as db, db.begin():
        await db.execute(
            update(PromptGeneration)
            .where(PromptGeneration.id == job["id"])
            .values(
                status="running",
                claim_token="old",
                lease_expires_at=utcnow() - timedelta(seconds=1),
            )
        )
    assert not await worker.instance.finish(job["id"], "old", status="succeeded", result=RESULT)
    await worker.instance.run_once()
    result = (await api.client.get(f"/ai/prompt-generations/{job['id']}")).json()
    assert result["status"] == "failed" and result["result"] is None
    assert worker.requests == []


async def test_prompt_migration_creates_and_removes_job_table():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0043_prompt_generation.py"  # noqa: ASYNC240
    spec = importlib.util.spec_from_file_location("prompt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    def check(conn):
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            columns = {c["name"] for c in inspect(conn).get_columns("prompt_generations")}
            assert {"requirement", "result", "claim_token", "config_snapshot"} <= columns
            assert inspect(conn).get_unique_constraints("prompt_generations")[0][
                "column_names"
            ] == ["admin_id", "request_id"]
            migration.downgrade()
            assert "prompt_generations" not in inspect(conn).get_table_names()

    async with engine.begin() as conn:
        await conn.run_sync(check)
    await engine.dispose()
