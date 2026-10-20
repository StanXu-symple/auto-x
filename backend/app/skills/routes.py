from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.models.ai import (
    AIFeature,
    AIListenTask,
    AIListenTaskSkill,
    AISetting,
    AISkill,
    AIUserSkillBinding,
)
from app.models.ai_data_source import AIDataSource
from app.models.monitored_user import MonitoredUser
from app.schemas.ai import (
    AIFeatureOut,
    AISettingsOut,
    AISettingsPatch,
    AISkillCreate,
    AISkillOut,
    AISkillPatch,
    AIUserSkillBindingOut,
    AIUserSkillBindingReplace,
)
from app.schemas.common import MessageResponse, Page
from app.services.ai_jobs import (
    get_ai_feature,
    get_ai_setting,
)
from app.skills.store import resolve_active_skills, resolve_context_skills

router = APIRouter(prefix="/ai", tags=["AI Creation"])
AI_HEARTBEAT_KEY = "xsentinel:ai-worker:heartbeat"


def _skill_out(skill: AISkill) -> AISkillOut:
    return AISkillOut.model_validate(skill)


async def _worker_readiness(redis: RedisClient, expected_provider: str) -> dict:
    try:
        raw = await redis.get(AI_HEARTBEAT_KEY)
        payload = json.loads(raw) if raw else None
        if not isinstance(payload, dict):
            raise ValueError("invalid heartbeat")
        timestamp = str(payload.get("last_heartbeat") or payload.get("timestamp", ""))
        last_heartbeat = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        provider_matches = payload.get("provider") == expected_provider
        key_configured = payload.get("key_configured") if provider_matches else None
        provider_ready = payload.get("provider_ready") if provider_matches else None
        key_required = payload.get("key_required") if provider_matches else None
        if key_configured is True:
            key_status = "configured"
        elif key_required is False:
            key_status = "not_required"
        elif key_configured is False:
            key_status = "missing"
        else:
            key_status = "unknown"
        return {
            "provider_ready": provider_ready if isinstance(provider_ready, bool) else None,
            "key_configured": key_configured if isinstance(key_configured, bool) else None,
            "key_status": key_status,
            "worker_status": "online",
            "worker_last_heartbeat": last_heartbeat,
        }
    except Exception:
        return {
            "provider_ready": None,
            "key_configured": None,
            "key_status": "worker_managed",
            "worker_status": "offline",
            "worker_last_heartbeat": None,
        }


async def _settings_out(setting: AISetting, redis: RedisClient) -> AISettingsOut:
    readiness = await _worker_readiness(redis, setting.provider)
    return AISettingsOut(
        enabled=setting.enabled,
        auto_generate=setting.auto_generate,
        auto_trigger_mode=setting.auto_trigger_mode,
        provider=setting.provider,
        model=setting.model_name,
        base_url=setting.base_url,
        bridge_url=setting.bridge_url,
        prompt_template=setting.prompt_template,
        language=setting.language,
        tone=setting.tone,
        max_attempts=setting.max_attempts,
        max_output_tokens=setting.max_output_tokens,
        request_timeout_seconds=setting.request_timeout_seconds,
        reasoning_effort=setting.reasoning_effort,
        default_skill_ids=setting.default_skill_ids or [],
        updated_at=setting.updated_at,
        **readiness,
    )


@router.get("/settings", response_model=AISettingsOut)
async def read_ai_settings(db: DbSession, redis: RedisClient, _: CurrentAdmin) -> AISettingsOut:
    setting = await get_ai_setting(db)
    return await _settings_out(setting, redis)


@router.patch("/settings", response_model=AISettingsOut)
async def patch_ai_settings(
    payload: AISettingsPatch,
    db: DbSession,
    redis: RedisClient,
    _: CurrentAdmin,
) -> AISettingsOut:
    changes = payload.model_dump(exclude_unset=True)
    data_source_fields = {"provider", "model", "base_url", "bridge_url"}
    if data_source_fields.intersection(changes):
        raise APIError(
            409,
            "ai_data_source_managed_separately",
            "模型、地址和 API Key 请在 AI 数据源菜单统一管理",
        )
    setting = await get_ai_setting(db, for_update=True)
    if changes.get("auto_trigger_mode") == "legacy_all":
        enabled_tasks = int(
            await db.scalar(
                select(func.count(AIListenTask.id)).where(AIListenTask.desired_state == "enabled")
            )
            or 0
        )
        if enabled_tasks:
            raise APIError(409, "enabled_listen_tasks_exist", "请先暂停所有已启用的监听任务")
    if "default_skill_ids" in changes:
        requested = changes["default_skill_ids"]
        skills = await resolve_active_skills(db, requested)
        if [skill.id for skill in skills] != requested:
            raise APIError(
                422,
                "invalid_skill_ids",
                "All default skills must exist and be active",
            )
    mapping = {"model": "model_name"}
    for key, value in changes.items():
        setattr(setting, mapping.get(key, key), value)
    if setting.enabled and await db.get(AIDataSource, 1) is None:
        raise APIError(
            409,
            "ai_data_source_required",
            "请先在 AI 数据源菜单配置并测试 OpenAI 兼容账号",
        )
    await db.commit()
    await db.refresh(setting)
    return await _settings_out(setting, redis)


@router.get("/skills", response_model=Page[AISkillOut])
async def list_ai_skills(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    active: bool | None = None,
) -> Page[AISkillOut]:
    conditions = [AISkill.is_active == active] if active is not None else []
    total = int(await db.scalar(select(func.count(AISkill.id)).where(*conditions)) or 0)
    skills = list(
        await db.scalars(
            select(AISkill)
            .where(*conditions)
            .order_by(AISkill.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(
        items=[_skill_out(skill) for skill in skills],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/skills", response_model=AISkillOut, status_code=status.HTTP_201_CREATED)
async def create_ai_skill(payload: AISkillCreate, db: DbSession, _: CurrentAdmin) -> AISkillOut:
    await get_ai_setting(db, for_update=True)
    skill = AISkill(**payload.model_dump(), version=1)
    db.add(skill)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "skill_name_exists", "A skill with this name already exists") from None
    await db.refresh(skill)
    return _skill_out(skill)


@router.get("/skills/{skill_id}", response_model=AISkillOut)
async def get_ai_skill(skill_id: int, db: DbSession, _: CurrentAdmin) -> AISkillOut:
    skill = await db.get(AISkill, skill_id)
    if skill is None:
        raise APIError(404, "skill_not_found", "AI skill was not found")
    return _skill_out(skill)


@router.patch("/skills/{skill_id}", response_model=AISkillOut)
async def patch_ai_skill(
    skill_id: int, payload: AISkillPatch, db: DbSession, _: CurrentAdmin
) -> AISkillOut:
    setting = await get_ai_setting(db, for_update=True)
    skill = await db.get(AISkill, skill_id, with_for_update=True)
    if skill is None:
        raise APIError(404, "skill_not_found", "AI skill was not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(skill, key, value)
    skill.version += 1
    if payload.is_active is False:
        setting.default_skill_ids = [
            selected for selected in (setting.default_skill_ids or []) if selected != skill_id
        ]
        await db.execute(
            update(AIUserSkillBinding)
            .where(AIUserSkillBinding.skill_id == skill_id)
            .values(is_active=False)
        )
        await db.execute(
            update(AIListenTask)
            .where(
                AIListenTask.id.in_(
                    select(AIListenTaskSkill.task_id).where(AIListenTaskSkill.skill_id == skill_id)
                )
            )
            .values(queue_hold_reason="skill_invalidated")
        )
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "skill_name_exists", "A skill with this name already exists") from None
    await db.refresh(skill)
    return _skill_out(skill)


@router.delete("/skills/{skill_id}", response_model=MessageResponse)
async def delete_ai_skill(skill_id: int, db: DbSession, _: CurrentAdmin) -> MessageResponse:
    setting = await get_ai_setting(db, for_update=True)
    skill = await db.get(AISkill, skill_id, with_for_update=True)
    if skill is None:
        raise APIError(404, "skill_not_found", "AI skill was not found")
    skill.is_active = False
    skill.version += 1
    setting.default_skill_ids = [
        selected for selected in (setting.default_skill_ids or []) if selected != skill_id
    ]
    await db.execute(
        update(AIUserSkillBinding)
        .where(AIUserSkillBinding.skill_id == skill_id)
        .values(is_active=False)
    )
    await db.execute(
        update(AIListenTask)
        .where(
            AIListenTask.id.in_(
                select(AIListenTaskSkill.task_id).where(AIListenTaskSkill.skill_id == skill_id)
            )
        )
        .values(queue_hold_reason="skill_invalidated")
    )
    await db.commit()
    return MessageResponse(message="AI skill deactivated")


@router.get("/features", response_model=list[AIFeatureOut])
async def list_ai_features(db: DbSession, _: CurrentAdmin) -> list[AIFeatureOut]:
    features = list(
        await db.scalars(
            select(AIFeature).where(AIFeature.is_active.is_(True)).order_by(AIFeature.id.asc())
        )
    )
    return [AIFeatureOut.model_validate(feature) for feature in features]


async def _binding_out(
    db: DbSession, user: MonitoredUser, feature: AIFeature
) -> AIUserSkillBindingOut:
    setting = await get_ai_setting(db)
    skills, source = await resolve_context_skills(
        db,
        monitored_user_id=user.id,
        feature=feature,
        fallback_skill_ids=setting.default_skill_ids or [],
    )
    return AIUserSkillBindingOut(
        monitored_user_id=user.id,
        username=user.username,
        feature=AIFeatureOut.model_validate(feature),
        skill_ids=[skill.id for skill in skills],
        skills=[_skill_out(skill) for skill in skills],
        resolution_source=source,
    )


@router.get(
    "/users/{user_id}/skill-bindings/{feature_code}",
    response_model=AIUserSkillBindingOut,
)
async def read_user_skill_binding(
    user_id: int, feature_code: str, db: DbSession, _: CurrentAdmin
) -> AIUserSkillBindingOut:
    await get_ai_setting(db, for_update=True)
    user = await db.get(MonitoredUser, user_id)
    if user is None:
        raise APIError(404, "monitored_user_not_found", "监听用户不存在")
    try:
        feature = await get_ai_feature(db, feature_code)
    except ValueError:
        raise APIError(404, "ai_feature_not_found", "AI 功能点不存在") from None
    return await _binding_out(db, user, feature)


@router.put(
    "/users/{user_id}/skill-bindings/{feature_code}",
    response_model=AIUserSkillBindingOut,
)
async def replace_user_skill_binding(
    user_id: int,
    feature_code: str,
    payload: AIUserSkillBindingReplace,
    db: DbSession,
    _: CurrentAdmin,
) -> AIUserSkillBindingOut:
    await get_ai_setting(db, for_update=True)
    user = await db.get(MonitoredUser, user_id)
    if user is None:
        raise APIError(404, "monitored_user_not_found", "监听用户不存在")
    try:
        feature = await get_ai_feature(db, feature_code)
    except ValueError:
        raise APIError(404, "ai_feature_not_found", "AI 功能点不存在") from None
    skills = await resolve_active_skills(db, payload.skill_ids)
    if [skill.id for skill in skills] != payload.skill_ids:
        raise APIError(422, "invalid_skill_ids", "所有 Skill 必须存在且已启用")
    await db.execute(
        delete(AIUserSkillBinding).where(
            AIUserSkillBinding.monitored_user_id == user.id,
            AIUserSkillBinding.ai_feature_id == feature.id,
        )
    )
    now = datetime.now(UTC)
    for priority, skill in enumerate(skills, start=1):
        db.add(
            AIUserSkillBinding(
                monitored_user_id=user.id,
                ai_feature_id=feature.id,
                skill_id=skill.id,
                priority=priority,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        )
    await db.commit()
    return await _binding_out(db, user, feature)
