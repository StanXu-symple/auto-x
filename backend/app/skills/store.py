import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AIFeature, AISkill, AIUserSkillBinding
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


async def resolve_context_skills(
    session: AsyncSession,
    *,
    monitored_user_id: int,
    feature: AIFeature,
    fallback_skill_ids: list[int],
    override_skill_ids: list[int] | None = None,
) -> tuple[list[AISkillOut], str]:
    if override_skill_ids is not None:
        return await resolve_active_skills(session, override_skill_ids), "manual_override"
    bound_ids = list(
        await session.scalars(
            select(AIUserSkillBinding.skill_id)
            .join(AISkill, AISkill.id == AIUserSkillBinding.skill_id)
            .where(
                AIUserSkillBinding.monitored_user_id == monitored_user_id,
                AIUserSkillBinding.ai_feature_id == feature.id,
                AIUserSkillBinding.is_active.is_(True),
                AISkill.is_active.is_(True),
            )
            .order_by(AIUserSkillBinding.priority.asc(), AIUserSkillBinding.id.asc())
        )
    )
    if bound_ids:
        return await resolve_active_skills(session, bound_ids), "user_feature_binding"
    return await resolve_active_skills(session, fallback_skill_ids), "global_default"
