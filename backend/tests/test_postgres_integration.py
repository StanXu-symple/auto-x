"""Opt-in PostgreSQL checks; each run creates and removes its own isolated schema."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.api.deps import get_db
from app.control_plane.auth_store import AuthStore
from app.core.config import Settings
from app.core.time import to_database_utc
from app.db import init_db
from app.db.base import Base
from app.models import (
    Admin,
    AgentConversation,
    AgentTurn,
    AIFeature,
    AIGenerationJob,
    AIListenTask,
    AIListenTaskSkill,
    AIListenTaskSubscription,
    AISetting,
    AISkill,
    MonitoredUser,
    ServiceAuthBootstrapState,
    ServiceAuthClientCredential,
    ServiceAuthGrant,
    Tweet,
)
from app.services import skill_client
from app.services.ai_jobs import enqueue_auto_jobs
from app.services.poller import PollingService
from app.skill_worker import internal, require_reader
from app.skills import bootstrap as skill_bootstrap

POSTGRES_DSN = os.getenv("TEST_POSTGRES_DSN")
BACKEND_DIR = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not POSTGRES_DSN, reason="Set TEST_POSTGRES_DSN to run")


@pytest.fixture(params=["alembic", "metadata"])
async def database(request, monkeypatch):
    schema = f"test_pg_{uuid.uuid4().hex}"
    control = create_async_engine(POSTGRES_DSN)
    engine = create_async_engine(
        POSTGRES_DSN, connect_args={"server_settings": {"search_path": schema}}
    )
    try:
        async with control.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            if request.param == "alembic":
                config = Config(str(BACKEND_DIR / "alembic.ini"))
                config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))

                def upgrade(sync_connection):
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)
            else:
                await connection.run_sync(Base.metadata.create_all)
            tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
            assert set(Base.metadata.tables) <= set(tables)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        monkeypatch.setattr(init_db, "AsyncSessionFactory", factory)
        monkeypatch.setattr(skill_bootstrap, "AsyncSessionFactory", factory)
        service = FastAPI()
        service.include_router(internal)

        async def service_session():
            async with factory() as session:
                yield session

        service.dependency_overrides[get_db] = service_session
        service.dependency_overrides[require_reader] = lambda: {"scope": "skills:read"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=service)) as http:
            client = skill_client.SkillClient(
                Settings(
                    _env_file=None,
                    nacos_config_enabled=False,
                    nacos_server_addr="",
                    skill_service_url="http://skill-test",
                ),
                http=http,
            )
            client.tokens = SimpleNamespace(token=AsyncMock(return_value="test"))
            monkeypatch.setattr(skill_client, "CLIENT", client)
            yield factory
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await control.dispose()


async def test_fresh_database_seed_and_business_writes(database):
    settings = Settings(_env_file=None)
    # Concurrent startup must leave exactly one administrator and preserve later edits.
    await asyncio.gather(*(init_db.seed_runtime_defaults(settings) for _ in range(2)))
    await asyncio.gather(*(skill_bootstrap.seed_skills() for _ in range(2)))
    async with database() as session, session.begin():
        assert await session.scalar(select(func.count(Admin.id))) == 1
        skill = await session.get(AISkill, 1)
        skill.instructions = "保留用户修改 🐘"
    await init_db.seed_runtime_defaults(settings)
    await skill_bootstrap.seed_skills()

    async with database() as session, session.begin():
        assert (await session.get(AISkill, 1)).instructions == "保留用户修改 🐘"
        custom_skills = [AISkill(name=f"自定义 {i}", instructions="中文 🚀") for i in range(4)]
        feature = AIFeature(code="custom", name="自定义功能", base_prompt="测试")
        user = MonitoredUser(username="postgres_test", x_user_id="42")
        session.add_all([*custom_skills, feature, user])
        await session.flush()
        assert min(skill.id for skill in custom_skills) >= 1000
        assert feature.id >= 1000
        assert user.is_active is True
        user_id = user.id

    service = PollingService(
        session_factory=database,
        redis=object(),
        x_client=object(),
        settings=settings,
        worker_id="postgres-integration",
    )
    payload = {
        "id": "1234567890123456789",
        "text": "PostgreSQL 中文与 emoji 🐘",
        "created_at": "2026-09-08T12:00:00+08:00",
        "entities": {"hashtags": [{"tag": "数据库"}]},
    }
    rows = [service._tweet_values(user_id, "42", payload, datetime.now(UTC))]
    async with database() as session, session.begin():
        assert await service._upsert_tweets(session, rows) == (1, [payload["id"]])
        assert await service._upsert_tweets(session, rows) == (0, [])

    async with database() as session, session.begin():
        boundary = datetime(2026, 9, 8, 12, tzinfo=timezone(timedelta(hours=8)))
        tweet = await session.scalar(
            select(Tweet).where(Tweet.posted_at >= to_database_utc(boundary))
        )
        assert tweet.posted_at == datetime(2026, 9, 8, 4, tzinfo=UTC)
        assert tweet.raw_payload["text"] == payload["text"]
        assert tweet.entities == payload["entities"]
        setting = await session.get(AISetting, 1)
        setting.enabled = True
        task = AIListenTask(
            name="PostgreSQL task",
            desired_state="enabled",
            listen_mode="original",
            effective_from=boundary - timedelta(hours=1),
            subscriptions=[AIListenTaskSubscription(monitored_user_id=user_id)],
            skills=[AIListenTaskSkill(skill_id=1, priority=1)],
        )
        session.add(task)
        tweet_id = tweet.id

    # skill-worker reads task selection through another database connection.
    async with database() as session, session.begin():
        tweet = await session.get(Tweet, tweet_id)
        assert await enqueue_auto_jobs(session, [tweet.id, tweet.id]) == 1
        assert await enqueue_auto_jobs(session, [tweet.id]) == 0
        job = await session.scalar(select(AIGenerationJob))
        assert job.skill_snapshot[0]["instructions"] == "保留用户修改 🐘"
        assert job.manual is False

    # Remove the task subscription before testing the tweet -> job cascade;
    # generation history intentionally prevents deleting the task itself.
    async with database() as session, session.begin():
        await session.execute(delete(AIListenTaskSubscription))
        await session.execute(delete(MonitoredUser).where(MonitoredUser.id == user_id))
        assert await session.scalar(select(func.count(Tweet.id))) == 0


async def test_agent_chat_constraints_and_completed_turn_releases_active_slot(database):
    # The fixture exercises both Alembic and metadata against PostgreSQL. The
    # default naming convention otherwise gives both compound uniques the same
    # first-column name, which SQLite accepts but PostgreSQL cannot create.
    async with database() as session, session.begin():
        connection = await session.connection()
        constraints = await connection.run_sync(
            lambda sync: inspect(sync).get_unique_constraints("agent_turns")
        )
        by_name = {item["name"]: tuple(item["column_names"]) for item in constraints}
        assert by_name == {
            "uq_agent_turn_request": ("conversation_id", "request_id"),
            "uq_agent_turn_sequence": ("conversation_id", "sequence"),
        }
        indexes = await connection.run_sync(
            lambda sync: inspect(sync).get_indexes("agent_turns")
        )
        active_index = next(item for item in indexes if item["name"] == "uq_agent_turn_active")
        assert active_index["unique"] is True
        assert active_index["column_names"] == ["conversation_id"]
        assert active_index["dialect_options"]["postgresql_where"]

        admin = Admin(username="agent-chat-constraints", password_hash="unused-test-hash")
        session.add(admin)
        await session.flush()
        conversations = [AgentConversation(admin_id=admin.id) for _ in range(2)]
        session.add_all(conversations)
        await session.flush()
        conversation_id, other_conversation_id = [item.id for item in conversations]
        request_id = str(uuid.uuid4())
        completed = AgentTurn(
            conversation_id=conversation_id,
            request_id=request_id,
            sequence=1,
            content="first completed turn",
            status="succeeded",
        )
        session.add(completed)
        await session.flush()

        async def reject_turn(constraint, **values):
            # A savepoint lets each real database rejection leave the remaining
            # behavior checks in the same transaction usable.
            with pytest.raises(IntegrityError, match=constraint):
                async with session.begin_nested():
                    session.add(AgentTurn(
                        conversation_id=conversation_id,
                        content="must be rejected",
                        **values,
                    ))
                    await session.flush()

        await reject_turn(
            "uq_agent_turn_request", request_id=request_id, sequence=2, status="succeeded"
        )
        await reject_turn(
            "uq_agent_turn_sequence",
            request_id=str(uuid.uuid4()), sequence=1, status="succeeded",
        )

        active = AgentTurn(
            conversation_id=conversation_id,
            request_id=str(uuid.uuid4()),
            sequence=2,
            content="active turn",
            status="queued",
        )
        session.add(active)
        await session.flush()
        for occupied_status in ("queued", "running"):
            active.status = occupied_status
            await session.flush()
            for new_status in ("queued", "running"):
                await reject_turn(
                    "uq_agent_turn_active",
                    request_id=str(uuid.uuid4()), sequence=3, status=new_status,
                )

        # All three restrictions are scoped to a conversation, so another
        # conversation may reuse the request/sequence and have an active turn.
        parallel = AgentTurn(
            conversation_id=other_conversation_id,
            request_id=request_id,
            sequence=1,
            content="independent conversation",
            status="queued",
        )
        session.add(parallel)
        await session.flush()

        active.status = "succeeded"
        await session.flush()
        following = AgentTurn(
            conversation_id=conversation_id,
            request_id=str(uuid.uuid4()),
            sequence=3,
            content="next turn after completion",
            status="queued",
        )
        session.add(following)
        await session.flush()
        assert await session.scalar(select(func.count()).select_from(AgentTurn)) == 4

    # Verify committed state through a new session, not just in-memory objects.
    async with database() as session:
        statuses = list(await session.scalars(
            select(AgentTurn.status)
            .where(AgentTurn.conversation_id == conversation_id)
            .order_by(AgentTurn.sequence)
        ))
        assert statuses == ["succeeded", "succeeded", "queued"]
        assert await session.scalar(
            select(AgentTurn.status).where(AgentTurn.conversation_id == other_conversation_id)
        ) == "queued"


async def test_service_auth_legacy_clients_bootstrap_is_exactly_once(
    database, tmp_path: Path
) -> None:
    clients_file = tmp_path / "clients.json"
    source_a = """{
          "backend": {
            "secret_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "grants": {"xhs-worker": "xhs:execute"}
          }
        }"""
    source_b = """{
          "backend": {
            "secret_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "grants": {"xhs-worker": "must:not-restore"}
          }
        }"""
    clients_file.write_text(source_a, encoding="utf-8")
    stores = [
        AuthStore(
            database,
            master_secret="a-shared-key-encryption-secret-with-32-bytes",
            legacy_clients_file=str(clients_file),
        )
        for _ in range(2)
    ]

    await asyncio.gather(*(store._bootstrap_legacy_clients() for store in stores))
    clients_file.write_text(source_b, encoding="utf-8")
    await stores[0]._bootstrap_legacy_clients()

    async with database() as session, session.begin():
        assert (
            await session.scalar(select(func.count()).select_from(ServiceAuthBootstrapState)) == 2
        )
        assert await session.scalar(select(func.count(ServiceAuthClientCredential.id))) == 2
        assert await session.scalar(select(func.count()).select_from(ServiceAuthGrant)) == 1
        grant = await session.get(ServiceAuthGrant, ("backend", "xhs-worker"))
        assert grant.scopes == "xhs:execute"
        await session.execute(delete(ServiceAuthGrant))
        await session.execute(delete(ServiceAuthClientCredential))

    clients_file.write_text(source_a, encoding="utf-8")
    await stores[0]._bootstrap_legacy_clients()
    clients_file.write_text(source_b, encoding="utf-8")
    await stores[0]._bootstrap_legacy_clients()

    async with database() as session:
        assert await session.scalar(select(func.count(ServiceAuthClientCredential.id))) == 0
        assert await session.scalar(select(func.count()).select_from(ServiceAuthGrant)) == 0


async def test_skill_http_reads_do_not_deadlock_and_mutations_wait_for_snapshot_commit(database):
    from app.models.ai import AIListenTask, AIListenTaskSkill
    from app.services.ai_jobs import get_ai_setting
    from app.skills.routes import delete_ai_skill

    settings = Settings(_env_file=None)
    await init_db.seed_runtime_defaults(settings)
    await skill_bootstrap.seed_skills()
    async with database() as session, session.begin():
        task = AIListenTask(name="锁测试", desired_state="enabled")
        task.skills = [AIListenTaskSkill(skill_id=1, priority=1)]
        session.add(task)
        await session.flush()
        task_id = task.id

    started = asyncio.Event()

    async def disable():
        async with database() as session:
            started.set()
            await delete_ai_skill(1, session, None)

    mutation = None
    try:
        async with database() as consumer, consumer.begin():
            await get_ai_setting(consumer, for_update=True)
            await consumer.get(AIListenTask, task_id, with_for_update=True)
            mutation = asyncio.create_task(disable())
            await started.wait()
            selection = await asyncio.wait_for(skill_client.CLIENT.task(task_id), timeout=2)
            assert selection.valid
            assert not mutation.done()
        await asyncio.wait_for(mutation, timeout=2)
        assert not (await skill_client.CLIENT.task(task_id)).valid
    finally:
        if mutation is not None and not mutation.done():
            mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)
