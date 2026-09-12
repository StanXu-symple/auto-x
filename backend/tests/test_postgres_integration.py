"""Opt-in PostgreSQL checks; each run creates and removes its own isolated schema."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.control_plane.auth_store import AuthStore
from app.core.config import Settings
from app.core.time import to_database_utc
from app.db import init_db
from app.db.base import Base
from app.models import (
    Admin,
    AIFeature,
    AIGenerationJob,
    AISetting,
    AISkill,
    MonitoredUser,
    ServiceAuthBootstrapState,
    ServiceAuthClientCredential,
    ServiceAuthGrant,
    Tweet,
)
from app.services.ai_jobs import enqueue_auto_jobs
from app.services.poller import PollingService

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
    async with database() as session, session.begin():
        assert await session.scalar(select(func.count(Admin.id))) == 1
        skill = await session.get(AISkill, 1)
        skill.instructions = "保留用户修改 🐘"
    await init_db.seed_runtime_defaults(settings)

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
        await session.flush()
        assert await enqueue_auto_jobs(session, [tweet.id, tweet.id]) == 1
        assert await enqueue_auto_jobs(session, [tweet.id]) == 0
        job = await session.scalar(select(AIGenerationJob))
        assert job.skill_snapshot[0]["instructions"] == "保留用户修改 🐘"
        assert job.manual is False

    # Verify FK actions against the real engine, bypassing ORM relationship cascades.
    async with database() as session, session.begin():
        await session.execute(delete(MonitoredUser).where(MonitoredUser.id == user_id))
        assert await session.scalar(select(func.count(Tweet.id))) == 0


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
