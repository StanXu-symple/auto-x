import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AISkill
from app.schemas.ai import AISkillOut
from app.skills.packages import read_package_instructions


async def resolve_active_skills(session: AsyncSession, skill_ids: list[int]) -> list[AISkillOut]:
    if not skill_ids:
        return []
    skills = list(
        await session.scalars(
            select(AISkill).where(AISkill.id.in_(skill_ids), AISkill.is_active.is_(True))
        )
    )
    by_id = {}
    for skill in skills:
        resolved = AISkillOut.model_validate(skill)
        if skill.source_type == "github":
            resolved.instructions = await asyncio.to_thread(
                read_package_instructions,
                skill.source_metadata,
            )
        by_id[skill.id] = resolved
    return [by_id[skill_id] for skill_id in skill_ids if skill_id in by_id]
