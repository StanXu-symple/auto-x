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

from app.api.deps import get_current_admin, get_current_admin_for_stream, get_db, get_redis
from app.api.errors import APIError
from app.api.routes import agent_chat
from app.core.config import Settings
from app.db.base import Base, utcnow
from app.models.admin import Admin
from app.models.agent_chat import AgentConversation, AgentTurn
from app.models.ai import AIDraft, AISetting
from app.schemas.agent_chat import AgentResult, SendAgentMessage
from app.schemas.ai import AISkillOut
from app.services import agent_chat_worker
from app.services.agent_chat_cleanup import AgentChatCleanup
from app.services.agent_chat_worker import AgentChatWorker
from app.services.agent_provider import chat_messages, generate_agent_reply, stream_agent_reply
from app.services.ai_data_source import AIDataSourceRuntime
from app.services.ai_provider import AIProviderClient, AIProviderError

CONFIG = {
    "language": "zh-CN",
    "tone": "自然",
    "reasoning_effort": "medium",
    "max_output_tokens": 4000,
    "timeout_seconds": 30,
}
ARTICLE = {"title": "AI 工作指南", "excerpt": "从实际任务开始。", "content": "这是第一版正文。"}
RUNTIME = AIDataSourceRuntime(
    "test", "openai_responses", "https://example.test/v1", "test-model", "test-key", 1
)
MIGRATION_PATH = Path(__file__).resolve().parents[1] / "alembic/versions/0040_agent_chat.py"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData(naming_convention=Base.metadata.naming_convention)
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    # SQLite only auto-increments a primary key spelled INTEGER.
    metadata.tables["ai_drafts"].c.id.type = Integer()
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: metadata.create_all(
                sync,
                tables=[
                    metadata.tables[name]
                    for name in (
                        "admins",
                        "ai_settings",
                        "ai_drafts",
                        "agent_conversations",
                        "agent_turns",
                    )
                ],
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        db.add_all(
            [
                Admin(id=1, username="first", password_hash="test"),
                Admin(id=2, username="second", password_hash="test"),
                AISetting(id=1, enabled=True),
            ]
        )
        await db.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def api(factory):
    app = FastAPI()
    app.include_router(agent_chat.router)

    async def session():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_current_admin_for_stream] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[get_redis] = lambda: None

    @app.exception_handler(APIError)
    async def handle(_request, exc):
        return JSONResponse(status_code=exc.status_code, content={"code": exc.code})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, app


def payload(content="写一篇文章", ids=None):
    return {"content": content, "skill_ids": ids or [], "request_id": str(uuid4())}


async def create(api):
    response = await api[0].post("/agent-chat/conversations")
    assert response.status_code == 201
    return "/agent-chat/conversations/" + response.json()["id"]


async def test_send_idempotent_and_owner_isolation(api, factory):
    client, app = api
    path = await create(api)
    body = payload()
    first = await client.post(path + "/messages", json=body)
    assert first.status_code == 202
    assert first.json()["status"] == "queued"
    assert "config_snapshot" not in first.json()
    assert (await client.post(path + "/messages", json=body)).json()["id"] == first.json()["id"]
    assert (await client.post(path + "/messages", json=payload())).status_code == 409
    assert (
        await client.post(path + "/messages", json={**body, "content": "不同"})
    ).status_code == 409
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentTurn)) == 1
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=2)
    assert (await client.get("/agent-chat/conversations")).json()["total"] == 0
    assert (await client.get(path)).status_code == 404
    assert (await client.post(path + "/messages", json=payload())).status_code == 404
    turn_path = path + "/messages/" + first.json()["id"]
    for operation in ("cancel", "retry", "article"):
        assert (await client.post(turn_path + "/" + operation)).status_code == 404


async def test_delete_conversation_removes_owned_turns_and_rejects_other_owner(api, factory):
    client, app = api
    path = await create(api)
    turn = (await client.post(path + "/messages", json=payload())).json()
    assert (await client.delete(path)).status_code == 204
    assert (await client.get(path)).status_code == 404
    async with factory() as db:
        assert await db.get(AgentTurn, turn["id"]) is None

    other = await create(api)
    app.dependency_overrides[get_current_admin] = lambda: SimpleNamespace(id=2)
    assert (await client.delete(other)).status_code == 404
    async with factory() as db:
        assert await db.get(AgentConversation, other.rsplit("/", 1)[-1]) is not None


async def test_delete_preserves_saved_article_and_clears_streams(api, factory):
    client, app = api
    path = await create(api)
    turn = (await client.post(path + "/messages", json=payload())).json()
    async with factory() as db:
        row = await db.get(AgentTurn, turn["id"])
        row.status, row.reply, row.article = "succeeded", "完成", ARTICLE
        await db.commit()
    saved = (await client.post(path + "/messages/" + turn["id"] + "/article")).json()
    redis = SimpleNamespace(delete=AsyncMock())
    app.dependency_overrides[get_redis] = lambda: redis
    assert (await client.delete(path)).status_code == 204
    redis.delete.assert_awaited_once_with(agent_chat_worker.event_stream_key(turn["id"]))
    async with factory() as db:
        assert await db.get(AgentTurn, turn["id"]) is None
        article = await db.get(AIDraft, int(saved["article_id"]))
        assert article.content == ARTICLE["content"]


async def test_delete_during_generation_discards_late_result(api, factory, monkeypatch):
    client, _ = api
    path = await create(api)
    turn = (await client.post(path + "/messages", json=payload())).json()
    runner = worker(factory)
    monkeypatch.setattr(agent_chat_worker, "get_ai_data_source", AsyncMock(return_value=RUNTIME))

    async def late_reply(*_):
        assert (await client.delete(path)).status_code == 204
        return AgentResult(reply="late result", article=ARTICLE)

    monkeypatch.setattr(agent_chat_worker, "stream_agent_reply", late_reply)
    await runner.run_once()
    async with factory() as db:
        assert await db.get(AgentTurn, turn["id"]) is None
        assert await db.get(AgentConversation, path.rsplit("/", 1)[-1]) is None
    runner.redis = SimpleNamespace(pipeline=lambda **_: pytest.fail("recreated deleted stream"))
    with pytest.raises(agent_chat_worker.AgentTurnRemoved):
        await runner.publish_event(turn["id"], {"type": "output", "delta": "late"}, attempts=1)


@pytest.mark.parametrize("remove_during_stream", [False, True])
async def test_worker_streams_current_attempt_and_stops_after_deletion(
    api, factory, monkeypatch, remove_during_stream
):
    client, _ = api
    path = await create(api)
    turn = (await client.post(path + "/messages", json=payload())).json()
    emitted = []

    class Pipeline:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def xadd(self, key, fields, **_):
            emitted.append(json.loads(fields["payload"]))

        def expire(self, *_):
            pass

        async def execute(self):
            pass

    runner = worker(factory)
    runner.redis = SimpleNamespace(pipeline=lambda **_: Pipeline())
    monkeypatch.setattr(agent_chat_worker, "get_ai_data_source", AsyncMock(return_value=RUNTIME))

    async def streaming(_provider, _runtime, _history, _turn, emit):
        await emit({"type": "output", "delta": "before deletion"})
        if remove_during_stream:
            assert (await client.delete(path)).status_code == 204
            await emit({"type": "output", "delta": "after deletion"})
            pytest.fail("generation did not stop after deletion")
        return AgentResult(reply="completed", article=ARTICLE)

    monkeypatch.setattr(agent_chat_worker, "stream_agent_reply", streaming)
    await runner.run_once()
    expected_events = ["status", "output"] + ([] if remove_during_stream else ["done"])
    assert [item["type"] for item in emitted] == expected_events
    assert all(item["attempts"] == 1 for item in emitted)
    assert runner.active_tasks == 0
    async with factory() as db:
        row = await db.get(AgentTurn, turn["id"])
        if remove_during_stream:
            assert row is None
        else:
            assert row.status == "succeeded" and row.reply == "completed"


async def test_stream_ends_when_conversation_is_deleted(api, factory, monkeypatch):
    client, app = api
    path = await create(api)
    turn = (await client.post(path + "/messages", json=payload())).json()
    monkeypatch.setattr(agent_chat, "AsyncSessionFactory", factory)

    async def replay(*_, **__):
        assert (await client.delete(path)).status_code == 204
        return []

    redis = SimpleNamespace(xrange=replay, delete=AsyncMock())
    app.dependency_overrides[get_redis] = lambda: redis
    response = await client.get(path + "/messages/" + turn["id"] + "/stream")
    assert '"type": "deleted"' in response.text
    assert "event: deleted" in response.text


async def test_agent_chat_retention_deletes_expired_inactive_rows(factory):
    now = utcnow()
    async with factory() as db:
        expired = AgentConversation(admin_id=1, updated_at=now - timedelta(days=8))
        active = AgentConversation(admin_id=1, updated_at=now - timedelta(days=8))
        recent = AgentConversation(admin_id=1, updated_at=now - timedelta(days=2))
        boundary = AgentConversation(admin_id=2, updated_at=now - timedelta(days=7))
        renewed = AgentConversation(admin_id=1, updated_at=now - timedelta(days=8))
        empty_old = AgentConversation(admin_id=2, updated_at=now - timedelta(days=8))
        db.add_all([expired, active, recent, boundary, renewed, empty_old])
        await db.flush()
        db.add_all(
            [
                AgentTurn(
                    conversation_id=expired.id,
                    request_id=str(uuid4()),
                    sequence=1,
                    content="expired",
                    status="succeeded",
                    updated_at=now - timedelta(days=8),
                ),
                AgentTurn(
                    conversation_id=active.id,
                    request_id=str(uuid4()),
                    sequence=1,
                    content="running",
                    status="running",
                    updated_at=now - timedelta(days=8),
                ),
                AgentTurn(
                    conversation_id=renewed.id,
                    request_id=str(uuid4()),
                    sequence=1,
                    content="recent reply",
                    status="succeeded",
                    updated_at=now - timedelta(hours=1),
                ),
            ]
        )
        await db.commit()

    class FakeRedis:
        def __init__(self):
            self.deleted = []

        async def delete(self, *keys):
            self.deleted.extend(keys)

    redis = FakeRedis()
    cleanup = AgentChatCleanup(
        Settings(_env_file=None, nacos_config_enabled=False, agent_chat_retention_days=7),
        factory,
        redis,
        asyncio.Event(),
    )
    assert await cleanup.run_once(now=now) == 2
    async with factory() as db:
        assert await db.get(AgentConversation, expired.id) is None
        assert await db.get(AgentConversation, active.id) is not None
        assert await db.get(AgentConversation, recent.id) is not None
        assert await db.get(AgentConversation, empty_old.id) is None
        assert await db.get(AgentConversation, boundary.id) is not None
        assert await db.get(AgentConversation, renewed.id) is not None
        assert (
            await db.scalar(select(AgentTurn.id).where(AgentTurn.conversation_id == expired.id))
            is None
        )
    assert redis.deleted


async def test_cleanup_reloads_nacos_policy_and_skips_bad_configuration(factory):
    now = utcnow()
    async with factory() as db:
        conversation = AgentConversation(admin_id=1, updated_at=now - timedelta(days=10))
        db.add(conversation)
        await db.commit()
    settings = Settings(_env_file=None, nacos_config_enabled=False, nacos_server_addr="")
    settings.nacos_config_enabled = True
    settings.nacos_server_addr = "http://nacos.test:8848"
    cleanup = AgentChatCleanup(settings, factory, None, asyncio.Event())
    remote = AsyncMock(return_value={"AGENT_CHAT_RETENTION_DAYS": 30})
    cleanup.config = SimpleNamespace(get_json_config=remote)
    assert await cleanup.run_once(now=now) == 0
    for invalid in [
        None,
        {"AGENT_CHAT_RETENTION_DAYS": 0},
        {"AGENT_CHAT_RETENTION_DAYS": True},
        {"AGENT_CHAT_RETENTION_DAYS": 1.5},
        {"AGENT_CHAT_RETENTION_DAYS": "bad"},
    ]:
        remote.return_value = invalid
        with pytest.raises(ValueError):
            await cleanup.run_once(now=now)
    remote.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        await cleanup.run_once(now=now)
    async with factory() as db:
        assert await db.get(AgentConversation, conversation.id) is not None
    remote.side_effect = None
    remote.return_value = {"agent_chat": {"retention_days": "7"}}
    assert await cleanup.run_once(now=now) == 1
    remote.return_value = {}
    assert await cleanup.retention_days() == 7


async def test_cleanup_batches_large_history(factory):
    async with factory() as db:
        db.add_all(
            [
                AgentConversation(admin_id=1, updated_at=utcnow() - timedelta(days=8))
                for _ in range(101)
            ]
        )
        await db.commit()
    cleanup = AgentChatCleanup(
        Settings(_env_file=None, nacos_config_enabled=False), factory, None, asyncio.Event()
    )
    assert await cleanup.run_once() == 100
    assert await cleanup.run_once() == 1
    assert await cleanup.run_once() == 0


@pytest.mark.parametrize("fail_cycle", [False, True])
async def test_cleanup_loop_handles_errors_and_closes_nacos_client(factory, fail_cycle):
    stop = asyncio.Event()
    cleanup = AgentChatCleanup(
        Settings(_env_file=None, nacos_config_enabled=False), factory, None, stop
    )
    cleanup.config = SimpleNamespace(aclose=AsyncMock())
    calls = 0

    async def sweep():
        nonlocal calls
        calls += 1
        if fail_cycle:
            stop.set()
            raise TimeoutError("Nacos unavailable")
        if calls == 1:
            return 100
        stop.set()
        return 1

    cleanup.run_once = sweep
    await asyncio.wait_for(cleanup.run(), timeout=1)
    assert calls == (1 if fail_cycle else 2)
    cleanup.config.aclose.assert_awaited_once()


async def test_reference_resolution_and_snapshot(api, factory, monkeypatch):
    prompt = AISkillOut(
        id=5,
        name="短文",
        description="",
        instructions="旧规则",
        is_active=True,
        version=2,
        content_type="prompt",
        created_at=utcnow(),
        updated_at=utcnow(),
        output_schema=None,
        remote_skill_id=None,
        remote_skill_version=None,
    )
    skill = AISkillOut(
        id=9,
        name="写作",
        description="",
        instructions="Skill 规则",
        is_active=True,
        version=4,
        content_type="skill",
        source_type="github",
        created_at=utcnow(),
        updated_at=utcnow(),
        output_schema=None,
        remote_skill_id=None,
        remote_skill_version=None,
    )
    resolver = AsyncMock(return_value=[prompt])
    monkeypatch.setattr(agent_chat, "get_skill_client", lambda: SimpleNamespace(resolve=resolver))
    path = await create(api)
    response = await api[0].post(path + "/messages", json=payload(ids=[5, 9]))
    assert response.status_code == 422
    resolver.return_value = [prompt, skill]
    response = await api[0].post(path + "/messages", json=payload(ids=[5, 9]))
    assert response.status_code == 202
    assert [s["content_type"] for s in response.json()["skill_snapshot"]] == ["prompt", "skill"]
    assert "instructions" not in response.json()["skill_snapshot"][0]
    prompt.instructions = "新规则"
    async with factory() as db:
        turn = await db.get(AgentTurn, response.json()["id"])
        assert turn.skill_snapshot[0]["instructions"] == "旧规则"
    assert (await api[0].post(path + "/messages", json=payload(ids=[5, 5]))).status_code == 422


async def test_retry_cancel_save_and_new_version(api, factory):
    client, _ = api
    path = await create(api)
    turn = (await client.post(path + "/messages", json=payload())).json()
    turn_path = path + "/messages/" + turn["id"]
    assert (await client.post(turn_path + "/article")).status_code == 409
    assert (await client.post(turn_path + "/cancel")).json()["status"] == "cancelled"
    assert (await client.post(turn_path + "/retry")).json()["status"] == "queued"
    async with factory() as db:
        row = await db.get(AgentTurn, turn["id"])
        row.status, row.reply, row.article = "succeeded", "完成", ARTICLE
        await db.commit()
    saved = (await client.post(turn_path + "/article")).json()
    assert isinstance(saved["article_id"], str)
    assert (await client.post(turn_path + "/article")).json() == saved
    assert (await client.get(path)).json()["turns"][0]["article_id"] == saved["article_id"]
    second = (await client.post(path + "/messages", json=payload("让开头更简洁"))).json()
    assert second["sequence"] == 2
    assert (await client.post(turn_path + "/retry")).status_code == 409
    async with factory() as db:
        row = await db.get(AgentTurn, second["id"])
        row.status, row.reply, row.article = (
            "succeeded",
            "修改完成",
            {**ARTICLE, "content": "新版本"},
        )
        await db.commit()
    saved2 = (await client.post(path + "/messages/" + second["id"] + "/article")).json()
    assert saved2 != saved
    async with factory() as db:
        articles = list(await db.scalars(select(AIDraft).order_by(AIDraft.id)))
        assert len(articles) == 2
        assert articles[0].content == ARTICLE["content"]
        assert articles[1].content == "新版本"
        assert all(row.article_source == "ai" and row.job_id is None for row in articles)


async def test_stream_returns_terminal_state_for_completed_turn(api, factory):
    client, _ = api
    path = await create(api)
    turn = (await client.post(path + "/messages", json=payload())).json()
    async with factory() as db:
        row = await db.get(AgentTurn, turn["id"])
        row.status, row.reply, row.article = "succeeded", "完成", None
        await db.commit()
    response = await client.get(path + "/messages/" + turn["id"] + "/stream")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: status" in response.text
    assert '"status": "succeeded"' in response.text
    assert "event: done" in response.text


async def queued_turn(factory):
    async with factory() as db:
        conversation = AgentConversation(admin_id=1)
        db.add(conversation)
        await db.flush()
        turn = AgentTurn(
            conversation_id=conversation.id,
            request_id=str(uuid4()),
            sequence=1,
            content="写一篇文章",
            config_snapshot=CONFIG,
            skill_snapshot=[],
        )
        db.add(turn)
        await db.commit()
        return turn


def worker(factory):
    return AgentChatWorker(
        Settings(_env_file=None), None, None, factory, asyncio.Semaphore(1), asyncio.Event()
    )


async def test_worker_multiturn_and_fenced_cancellation(factory, monkeypatch):
    first = await queued_turn(factory)
    runner = worker(factory)
    monkeypatch.setattr(agent_chat_worker, "get_ai_data_source", AsyncMock(return_value=RUNTIME))
    generate = AsyncMock(return_value=AgentResult(reply="完成", article=ARTICLE))

    async def stream(*args):
        return await generate(*args[:4])

    monkeypatch.setattr(agent_chat_worker, "stream_agent_reply", stream)
    await runner.run_once()
    async with factory() as db:
        row = await db.get(AgentTurn, first.id)
        assert row.status == "succeeded" and row.attempts == 1 and row.article == ARTICLE
        second = AgentTurn(
            conversation_id=first.conversation_id,
            request_id=str(uuid4()),
            sequence=2,
            content="修改开头",
            config_snapshot=CONFIG,
            skill_snapshot=[],
        )
        db.add(second)
        await db.commit()

    async def cancelled_during_generation(_provider, _runtime, history, current):
        assert len(history) == 1 and history[0].article == ARTICLE
        async with factory() as db:
            row = await db.get(AgentTurn, current.id)
            row.status, row.claim_token = "cancelled", None
            await db.commit()
        return AgentResult(reply="迟到结果", article=ARTICLE)

    generate.side_effect = cancelled_during_generation
    await runner.run_once()
    async with factory() as db:
        row = await db.get(AgentTurn, second.id)
        assert row.status == "cancelled" and row.reply is None
    # Already finished/cancelled messages are never claimed again.
    await runner.run_once()
    assert generate.await_count == 2


async def test_expired_lease_and_old_token_cannot_finish(factory):
    turn = await queued_turn(factory)
    runner = worker(factory)
    async with factory() as db:
        row = await db.get(AgentTurn, turn.id)
        row.status, row.claim_token = "running", "old-token"
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        await db.commit()
    assert not await runner.finish(turn.id, "old-token", status="succeeded", reply="late")
    await runner.run_once()
    async with factory() as db:
        row = await db.get(AgentTurn, turn.id)
        assert row.status == "failed" and row.claim_token is None
        row.status, row.claim_token = "running", "new-token"
        row.lease_expires_at = utcnow() + timedelta(seconds=30)
        await db.commit()
    assert not await runner.finish(turn.id, "old-token", status="succeeded", reply="late")
    assert await runner.finish(turn.id, "new-token", status="succeeded", reply="new")


async def test_worker_does_not_store_provider_secrets(factory, monkeypatch):
    turn = await queued_turn(factory)
    monkeypatch.setattr(agent_chat_worker, "get_ai_data_source", AsyncMock(return_value=RUNTIME))
    monkeypatch.setattr(
        agent_chat_worker,
        "stream_agent_reply",
        AsyncMock(
            side_effect=AIProviderError(
                "HTTP error: secret-key-and-prompt", retryable=True, status_code=401
            )
        ),
    )
    await worker(factory).run_once()
    async with factory() as db:
        row = await db.get(AgentTurn, turn.id)
        assert row.status == "failed" and "401" in row.error and "secret" not in row.error


async def test_worker_shutdown_requeues_and_shares_generation_capacity(factory, monkeypatch):
    turn = await queued_turn(factory)
    runner = worker(factory)
    entered = asyncio.Event()

    async def slow_model(*_args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(agent_chat_worker, "get_ai_data_source", AsyncMock(return_value=RUNTIME))
    monkeypatch.setattr(agent_chat_worker, "stream_agent_reply", slow_model)
    # Simulate an existing tweet generation holding the shared model slot.
    await runner.semaphore.acquire()
    task = asyncio.create_task(runner.process(turn.id))
    await asyncio.sleep(0)
    async with factory() as db:
        assert (await db.get(AgentTurn, turn.id)).status == "queued"
    runner.semaphore.release()
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    async with factory() as db:
        row = await db.get(AgentTurn, turn.id)
        assert row.status == "queued" and row.claim_token is None and row.attempts == 1
    assert runner.active_tasks == 0


async def test_disabled_ai_is_rejected_before_enqueue(api, factory):
    async with factory() as db:
        (await db.get(AISetting, 1)).enabled = False
        await db.commit()
    path = await create(api)
    response = await api[0].post(path + "/messages", json=payload())
    assert response.status_code == 409 and response.json()["code"] == "ai_disabled"
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentTurn)) == 0


async def test_provider_responses_contract_and_article_history():
    seen = []

    async def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps({"reply": "已修改", "article": ARTICLE}),
                            }
                        ],
                    }
                ],
            },
        )

    current = AgentTurn(
        content="修改正文",
        config_snapshot=CONFIG,
        skill_snapshot=[
            {
                "id": 1,
                "name": "简洁",
                "content_type": "prompt",
                "instructions": "减少空话",
                "version": 2,
            }
        ],
    )
    history = [AgentTurn(content="开始写", reply="已写好", article=ARTICLE)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        provider = AIProviderClient(Settings(_env_file=None), http)
        result = await generate_agent_reply(provider, RUNTIME, history, current)
    assert result.article.title == ARTICLE["title"]
    sent = seen[0]
    assert sent["store"] is False
    assert [item["role"] for item in sent["input"]] == ["user", "assistant", "user"]
    assert ARTICLE["content"] in sent["input"][1]["content"]
    assert "减少空话" in sent["instructions"]
    schema = sent["text"]["format"]["schema"]
    assert schema["required"] == ["reply", "article"]
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["AgentArticle"]["additionalProperties"] is False


async def test_provider_stream_emits_reasoning_tool_and_output_events():
    streamed = []

    async def collect(event):
        streamed.append(event)

    output = json.dumps({"reply": "流式完成", "article": ARTICLE}, ensure_ascii=False)
    frames = [
        {"type": "response.created"},
        {"type": "response.output_item.added", "item": {"type": "reasoning"}},
        {"type": "response.reasoning_summary_text.delta", "delta": "先梳理结构。"},
        {
            "type": "response.output_item.added",
            "item": {
                "type": "function_call",
                "id": "item-1",
                "name": "文章结构检查",
                "call_id": "call-1",
            },
        },
        {
            "type": "response.function_call_arguments.delta",
            "item_id": "item-1",
            "delta": '{"section":1}',
        },
        {"type": "response.output_text.delta", "delta": output[:20]},
        {"type": "response.output_text.delta", "delta": output[20:]},
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    body = "".join(
        f"event: {frame['type']}\ndata: {json.dumps(frame, ensure_ascii=False)}\n\n"
        for frame in frames
    )

    async def respond(request):
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["reasoning"]["summary"] == "auto"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        result = await stream_agent_reply(
            AIProviderClient(Settings(_env_file=None), http),
            RUNTIME,
            [],
            AgentTurn(content="写作", config_snapshot=CONFIG, skill_snapshot=[]),
            collect,
        )
    assert result.reply == "流式完成"
    types = [event["type"] for event in streamed]
    assert types.count("reasoning") == 1
    assert types.count("tool") == 2
    assert types.count("output") == 2
    assert streamed[types.index("reasoning")]["delta"] == "先梳理结构。"
    tool_arguments = next(
        event for event in streamed if event["type"] == "tool" and event["phase"] == "arguments"
    )
    assert tool_arguments["call_id"] == "call-1"


@pytest.mark.parametrize(
    "raw",
    [
        {"status": "incomplete", "output_text": json.dumps({"reply": "部分", "article": None})},
        {"status": "completed", "output_text": "{}"},
        {"output_text": "not json"},
        {"output": [{"content": [{"type": "refusal", "refusal": "no"}]}]},
        {
            "output_text": json.dumps({"reply": "done", "article": None}),
            "output": [{"content": [{"type": "refusal", "refusal": "no"}]}],
        },
    ],
)
async def test_invalid_or_incomplete_provider_output_is_rejected(raw):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw))
    ) as http:
        with pytest.raises(AIProviderError):
            await generate_agent_reply(
                AIProviderClient(Settings(_env_file=None), http),
                RUNTIME,
                [],
                AgentTurn(content="写作", config_snapshot=CONFIG, skill_snapshot=[]),
            )


def test_long_history_keeps_latest_article_and_current_request():
    history = [AgentTurn(content="开始", reply="完成", article=ARTICLE)] + [
        AgentTurn(content="澄清" * 5000, reply="继续", article=None) for _ in range(20)
    ]
    messages = chat_messages(history, AgentTurn(content="现在修改文章"))
    assert ARTICLE["content"] in messages[0]["content"]
    assert messages[-1]["content"] == "现在修改文章"
    assert len(messages) <= 26


async def test_migration_upgrade_downgrade():
    spec = importlib.util.spec_from_file_location("agent_chat_migration", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    retention_path = MIGRATION_PATH.parent / "0041_agent_chat_retention.py"
    retention_spec = importlib.util.spec_from_file_location("agent_chat_retention", retention_path)
    retention = importlib.util.module_from_spec(retention_spec)
    retention_spec.loader.exec_module(retention)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    def roundtrip(connection):
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert "agent_turns" in inspect(connection).get_table_names()
            retention.upgrade()
            assert any(
                item["name"] == "ix_agent_conversations_updated_at"
                for item in inspect(connection).get_indexes("agent_conversations")
            )
            retention.downgrade()
            indexes = inspect(connection).get_indexes("agent_turns")
            assert next(item for item in indexes if item["name"] == "uq_agent_turn_active")[
                "unique"
            ]
            migration.downgrade()
            assert "agent_turns" not in inspect(connection).get_table_names()

    async with engine.begin() as connection:
        await connection.run_sync(roundtrip)
    await engine.dispose()


def test_blank_message_rejected():
    with pytest.raises(ValueError):
        SendAgentMessage(request_id=uuid4(), content="   ")
