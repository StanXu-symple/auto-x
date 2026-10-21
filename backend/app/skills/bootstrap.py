"""Idempotent bundled Skill initialization, owned by skill-worker."""

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as postgres_insert

from app.db.session import AsyncSessionFactory
from app.models.ai import AISkill
from app.services.ai_defaults import DEFAULT_AI_SKILLS


async def seed_skills() -> None:
    async with AsyncSessionFactory() as session, session.begin():
        # Use fixed IDs and no-op updates so startup races are harmless and administrator
        # edits to the bundled skills are never overwritten on subsequent restarts.
        now = datetime.now(UTC)
        for skill in DEFAULT_AI_SKILLS:
            statement = postgres_insert(AISkill).values(
                **skill,
                content_type="prompt",
                source_type="custom",
                output_schema=None,
                is_active=True,
                version=1,
                remote_skill_id=None,
                remote_skill_version=None,
                created_at=now,
                updated_at=now,
            )
            # Both id and name are unique. Concurrent replica startups can race
            # on either index; a targetless upsert preserves existing rows on both.
            statement = statement.on_conflict_do_nothing()
            await session.execute(statement)
