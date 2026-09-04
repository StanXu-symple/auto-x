import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from app.core.config import Settings
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import AsyncSessionFactory, engine
from app.models import (  # imports all model metadata
    Admin,
    AIFeature,
    AISetting,
    AISkill,
    AppSetting,
)
from app.services.ai_defaults import DEFAULT_AI_FEATURES, DEFAULT_AI_MODEL, DEFAULT_AI_SKILLS

logger = logging.getLogger(__name__)


async def initialize_database(settings: Settings) -> None:
    if settings.auto_create_tables:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    await seed_runtime_defaults(settings)


async def seed_runtime_defaults(settings: Settings, *, seed_admin: bool = True) -> None:
    """Seed rows with MySQL upserts so concurrent API startups remain idempotent."""
    async with AsyncSessionFactory() as session, session.begin():
        admin = await session.scalar(select(Admin).where(Admin.username == settings.admin_username))
        if seed_admin and admin is None:
            now = datetime.now(UTC)
            statement = mysql_insert(Admin).values(
                username=settings.admin_username,
                password_hash=hash_password(settings.admin_password),
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            statement = statement.on_duplicate_key_update(username=statement.inserted.username)
            await session.execute(statement)
            logger.info(
                "Initial administrator seed ensured",
                extra={"username": settings.admin_username},
            )

        polling = await session.get(AppSetting, "polling")
        if polling is None:
            statement = mysql_insert(AppSetting).values(
                key="polling",
                value={
                    "global_poll_interval_seconds": settings.default_poll_interval_seconds,
                    "max_concurrency": settings.worker_max_concurrency,
                },
                updated_at=datetime.now(UTC),
            )
            statement = statement.on_duplicate_key_update(key=statement.inserted.key)
            await session.execute(statement)

        x_source = await session.get(AppSetting, "x_source")
        if x_source is None:
            statement = mysql_insert(AppSetting).values(
                key="x_source",
                value={"provider": "official_api"},
                updated_at=datetime.now(UTC),
            )
            statement = statement.on_duplicate_key_update(key=statement.inserted.key)
            await session.execute(statement)

        # Use fixed IDs and no-op updates so startup races are harmless and administrator
        # edits to the bundled skills are never overwritten on subsequent restarts.
        now = datetime.now(UTC)
        for skill in DEFAULT_AI_SKILLS:
            statement = mysql_insert(AISkill).values(
                **skill,
                output_schema=None,
                is_active=True,
                version=1,
                remote_skill_id=None,
                remote_skill_version=None,
                created_at=now,
                updated_at=now,
            )
            statement = statement.on_duplicate_key_update(id=AISkill.id)
            await session.execute(statement)

        for feature in DEFAULT_AI_FEATURES:
            statement = mysql_insert(AIFeature).values(
                **feature,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            statement = statement.on_duplicate_key_update(id=AIFeature.id)
            await session.execute(statement)

        statement = mysql_insert(AISetting).values(
            id=1,
            enabled=False,
            auto_generate=True,
            provider="openai_responses",
            model=DEFAULT_AI_MODEL,
            base_url=settings.openai_base_url,
            bridge_url=settings.codex_bridge_url or None,
            prompt_template=None,
            language="zh-CN",
            tone="专业自然",
            require_review=True,
            reasoning_effort="medium",
            default_skill_ids=[1],
            max_attempts=3,
            max_output_tokens=2500,
            request_timeout_seconds=60,
            updated_at=now,
        )
        statement = statement.on_duplicate_key_update(id=AISetting.id)
        await session.execute(statement)
