"""Workflow API, persistence and model protocol tests without external model calls."""

import asyncio
import importlib.util
import json
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
from sqlalchemy import Integer, MetaData, func, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.deps import get_current_admin, get_db
from app.api.errors import APIError
from app.api.routes import agent_workflow_runs, agent_workflows
from app.core.config import Settings
from app.db.base import Base, utcnow
from app.models import (
    Admin,
    AgentWorkflowRun,
    AgentWorkflowStepRun,
    AIDataSource,
    AIDraft,
    AISetting,
)
from app.schemas.ai import AISkillOut
from app.services import agent_workflow_worker, skill_client
from app.services.agent_workflow_worker import AgentWorkflowWorker
from app.services.ai_data_source import AIDataSourceRuntime
from app.services.ai_provider import AIProviderClient

ARTICLE = {"title": "第一版", "content": "原始文章正文", "excerpt": "摘要"}
REVIEW = {
    "score": 65,
    "decision": "revise",
    "summary": "需要补充依据",
    "issues": ["缺少来源"],
    "suggestions": ["保留不确定性"],
}
RUNTIME = AIDataSourceRuntime(
    "test", "openai_responses", "https://example.test/v1", "test-model", "secret-key", 1
)
WORKFLOW_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic/versions/0042_agent_workflows.py"
)


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    for name in ("ai_drafts", "ai_agent_workflow_steps"):
        metadata.tables[name].c.id.type = Integer()
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db, db.begin():
        db.add_all(
            [
                Admin(id=1, username="test", password_hash="unused"),
                Admin(id=2, username="other", password_hash="unused"),
                AISetting(id=1, enabled=True),
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
async def api(factory, monkeypatch):
    app = FastAPI()
    app.include_router(agent_workflows.router)
    app.include_router(agent_workflow_runs.router)

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=1)
    skills = {
        i: AISkillOut(
            id=i,
            name=f"提示词 {i}",
            description=None,
            instructions=f"指令 {i}",
            output_schema=None,
            version=1,
            is_active=True,
            remote_skill_id=None,
            remote_skill_version=None,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        for i in (1, 2)
    }

    async def resolve(ids):
        return [skills[i] for i in ids if i in skills and skills[i].is_active]

    monkeypatch.setattr(
        skill_client, "CLIENT", SimpleNamespace(resolve=AsyncMock(side_effect=resolve))
    )

    @app.exception_handler(APIError)
    async def error(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(client=client, app=app, skills=skills)


async def definitions(api, types=("article", "review")):
    nodes = []
    for index, kind in enumerate(types):
        response = await api.client.post(
            "/ai/agent-nodes",
            json={
                "name": f"节点 {index}",
                "node_type": kind,
                "skill_ids": [1, 2],
            },
        )
        assert response.status_code == 201, response.text
        nodes.append(response.json())
    response = await api.client.post(
        "/ai/agent-workflows",
        json={
            "name": "文章流程",
            "node_ids": [n["id"] for n in nodes],
        },
    )
    assert response.status_code == 201, response.text
    return nodes, response.json()


async def new_run(api, flow, **kwargs):
    payload = {
        "workflow_id": flow["id"],
        "workflow_version": flow["version"],
        "request_id": str(uuid4()),
        "input_text": "生成后审核",
        **kwargs,
    }
    response = await api.client.post("/ai/agent-workflow-runs", json=payload)
    assert response.status_code == 202, response.text
    return response.json()["id"], payload


def worker(factory, monkeypatch, outputs):
    requests = []

    async def model(request):
        requests.append(json.loads(request.content))
        output = outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_text": json.dumps(output, ensure_ascii=False),
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        )

    settings = Settings(_env_file=None, nacos_config_enabled=False)
    provider = AIProviderClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(model)))
    monkeypatch.setattr(
        agent_workflow_worker, "get_ai_data_source", AsyncMock(return_value=RUNTIME)
    )
    return AgentWorkflowWorker(
        settings, None, provider, factory, asyncio.Semaphore(2), asyncio.Event()
    ), requests


async def test_edit_chain_preserves_order_repeated_nodes_and_versions(api):
    nodes, flow = await definitions(api)
    payload = {
        "name": flow["name"],
        "version": 1,
        "node_ids": [nodes[1]["id"], nodes[0]["id"], nodes[1]["id"]],
    }
    response = await api.client.put(f"/ai/agent-workflows/{flow['id']}", json=payload)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["version"] == 2
    assert [n["id"] for n in updated["nodes"]] == payload["node_ids"]
    assert (
        await api.client.put(f"/ai/agent-workflows/{flow['id']}", json=payload)
    ).status_code == 409
    assert (
        await api.client.post(
            "/ai/agent-nodes",
            json={
                "name": "missing",
                "skill_ids": [999],
            },
        )
    ).status_code == 422
    await api.client.delete(f"/ai/agent-nodes/{nodes[0]['id']}")
    result = await api.client.post(
        "/ai/agent-workflows",
        json={
            "name": "不可用节点",
            "node_ids": [nodes[0]["id"]],
        },
    )
    assert result.status_code == 422


async def test_run_freezes_context_is_idempotent_and_saves_article_after_review(
    api, factory, monkeypatch
):
    nodes, flow = await definitions(api)
    run_id, payload = await new_run(api, flow)
    api.skills[1].instructions = "运行后修改，不得影响快照"
    api.skills[1].version = 2
    await api.client.delete(f"/ai/agent-nodes/{nodes[0]['id']}")
    repeated = await api.client.post("/ai/agent-workflow-runs", json=payload)
    assert repeated.json()["id"] == run_id
    assert (
        await api.client.post(
            "/ai/agent-workflow-runs",
            json={
                **payload,
                "input_text": "changed",
            },
        )
    ).status_code == 409
    runner, requests = worker(factory, monkeypatch, [ARTICLE, REVIEW])
    await runner.process(run_id)
    await runner.process(run_id)  # A completed claim cannot execute twice.
    result = (await api.client.get(f"/ai/agent-workflow-runs/{run_id}")).json()
    assert result["status"] == "succeeded"
    assert len(requests) == 2
    assert "运行后修改" not in requests[0]["instructions"]
    context = json.loads(requests[1]["input"][0]["content"])
    assert context["task"] == payload["input_text"]
    assert context["previous_steps"][0]["output"] == ARTICLE
    assert result["steps"][1]["output"] == REVIEW
    assert "secret-key" not in json.dumps(result)
    saved = await api.client.post(f"/ai/agent-workflow-runs/{run_id}/article")
    assert saved.status_code == 200, saved.text
    assert (
        await api.client.post(f"/ai/agent-workflow-runs/{run_id}/article")
    ).json() == saved.json()
    article_id = saved.json()["article_id"]
    async with factory() as db:
        article = await db.get(AIDraft, article_id)
        assert article.content == ARTICLE["content"]
        assert article.publish_status == "unpublished"
        assert article.draft_metadata["agent_workflow_run_id"] == run_id
        assert await db.scalar(select(func.count(AIDraft.id))) == 1
    runs = (
        await api.client.get("/ai/agent-workflow-runs", params={"article_id": article_id})
    ).json()
    assert [row["id"] for row in runs["items"]] == [run_id]
    await runner.provider.client.aclose()


async def test_retry_resumes_failed_node_with_prior_output(api, factory, monkeypatch):
    _, flow = await definitions(api)
    run_id, _ = await new_run(api, flow)
    runner, requests = worker(factory, monkeypatch, [ARTICLE, {"score": 101}, REVIEW])
    await runner.process(run_id)
    failed = (await api.client.get(f"/ai/agent-workflow-runs/{run_id}")).json()
    assert failed["status"] == "retry_wait"
    assert failed["steps"][0]["status"] == "succeeded"
    async with factory() as db, db.begin():
        run = await db.get(AgentWorkflowRun, run_id)
        run.next_attempt_at = utcnow() - timedelta(seconds=1)
    await runner.run_once()
    result = (await api.client.get(f"/ai/agent-workflow-runs/{run_id}")).json()
    assert result["status"] == "succeeded"
    assert [s["attempts"] for s in result["steps"]] == [1, 2]
    assert len(requests) == 3
    assert json.loads(requests[2]["input"][0]["content"])["previous_steps"][0]["output"] == ARTICLE
    await runner.provider.client.aclose()


async def test_cancel_fences_late_model_result_and_other_admin_cannot_access(
    api, factory, monkeypatch
):
    _, flow = await definitions(api, ("custom",))
    run_id, _ = await new_run(api, flow)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(*_args):
        entered.set()
        await release.wait()
        return {"text": "late"}, {}

    runner, _ = worker(factory, monkeypatch, [])
    monkeypatch.setattr(agent_workflow_worker, "execute_node", slow)
    running = asyncio.create_task(runner.process(run_id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert (
            await api.client.post(f"/ai/agent-workflow-runs/{run_id}/cancel")
        ).status_code == 200
        release.set()
        await asyncio.wait_for(running, 2)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
    result = (await api.client.get(f"/ai/agent-workflow-runs/{run_id}")).json()
    assert result["status"] == "cancelled"
    assert result["steps"][0]["output"] is None
    assert (await api.client.post(f"/ai/agent-workflow-runs/{run_id}/retry")).status_code == 409
    api.app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=2)
    for method, suffix in (
        ("GET", ""),
        ("POST", "/cancel"),
        ("POST", "/retry"),
        ("POST", "/article"),
    ):
        assert (
            await api.client.request(method, f"/ai/agent-workflow-runs/{run_id}{suffix}")
        ).status_code == 404
    await runner.provider.client.aclose()


async def test_stale_lease_recovery_and_manual_retry_keep_successful_steps(
    api, factory, monkeypatch
):
    _, flow = await definitions(api)
    run_id, _ = await new_run(api, flow)
    async with factory() as db, db.begin():
        run = await db.get(AgentWorkflowRun, run_id)
        run.status, run.claim_token = "running", "old-worker"
        run.lease_expires_at = utcnow() - timedelta(seconds=1)
        first = await db.get(AgentWorkflowStepRun, (run_id, 1))
        first.status, first.output, first.attempts = "succeeded", ARTICLE, 1
        second = await db.get(AgentWorkflowStepRun, (run_id, 2))
        second.status, second.attempts = "running", 3
    runner, requests = worker(factory, monkeypatch, [REVIEW])
    await runner.run_once()
    assert (await api.client.get(f"/ai/agent-workflow-runs/{run_id}")).json()["status"] == "failed"
    assert (await api.client.post(f"/ai/agent-workflow-runs/{run_id}/retry")).status_code == 200
    await runner.run_once()
    result = (await api.client.get(f"/ai/agent-workflow-runs/{run_id}")).json()
    assert result["status"] == "succeeded"
    assert len(requests) == 1
    assert result["steps"][0]["output"] == ARTICLE
    await runner.provider.client.aclose()


async def test_article_revision_checks_and_review_only_cannot_be_saved(api, factory, monkeypatch):
    _, flow = await definitions(api, ("review",))
    async with factory() as db, db.begin():
        article = AIDraft(**ARTICLE)
        db.add(article)
        await db.flush()
        article_id = article.id
    run_id, payload = await new_run(api, flow, article_id=article_id, article_revision=1)
    async with factory() as db, db.begin():
        article = await db.get(AIDraft, article_id)
        article.revision, article.content = 2, "changed"
    stale = await api.client.post(
        "/ai/agent-workflow-runs", json={**payload, "request_id": str(uuid4())}
    )
    assert stale.status_code == 409
    runner, requests = worker(factory, monkeypatch, [REVIEW])
    await runner.process(run_id)
    result = (await api.client.get(f"/ai/agent-workflow-runs/{run_id}")).json()
    assert result["source_changed"] is True
    assert json.loads(requests[0]["input"][0]["content"])["source"]["content"] == ARTICLE["content"]
    assert (await api.client.post(f"/ai/agent-workflow-runs/{run_id}/article")).status_code == 409
    await runner.provider.client.aclose()


async def test_model_change_fails_clearly_without_calling_new_endpoint(api, factory, monkeypatch):
    _, flow = await definitions(api, ("custom",))
    run_id, _ = await new_run(api, flow)
    runner, requests = worker(factory, monkeypatch, [])
    monkeypatch.setattr(
        agent_workflow_worker,
        "get_ai_data_source",
        AsyncMock(
            return_value=AIDataSourceRuntime(
                "new", RUNTIME.protocol, RUNTIME.base_url, "new-model", "new-key", 2
            )
        ),
    )
    await runner.process(run_id)
    result = (await api.client.get(f"/ai/agent-workflow-runs/{run_id}")).json()
    assert result["status"] == "failed"
    assert "模型已改变" in result["error"]
    assert not requests
    await runner.provider.client.aclose()


async def test_migration_upgrade_downgrade():
    spec = importlib.util.spec_from_file_location("workflow_migration", WORKFLOW_MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:

        def verify(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()
                assert "agent_workflow_runs" in inspect(sync).get_table_names()
                migration.downgrade()
                assert inspect(sync).get_table_names() == []

        await connection.run_sync(verify)
    await engine.dispose()
