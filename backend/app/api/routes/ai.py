from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.core.config import get_settings
from app.models.ai import AIDraft, AIGenerationJob, AISetting, AISkill
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.schemas.ai import (
    AIDraftOut,
    AIDraftPatch,
    AIJobDetail,
    AIJobOut,
    AISettingsOut,
    AISettingsPatch,
    AISkillCreate,
    AISkillOut,
    AISkillPatch,
    ManualGenerateRequest,
)
from app.schemas.common import MessageResponse, Page
from app.services.ai_jobs import create_manual_job, get_ai_setting, resolve_active_skills

router = APIRouter(prefix="/ai", tags=["AI Creation"])
tweets_router = APIRouter(prefix="/tweets", tags=["AI Creation"])
AI_HEARTBEAT_KEY = "xsentinel:ai-worker:heartbeat"


def _ensure_provider_host_allowed(setting: AISetting) -> None:
    destination = setting.base_url if setting.provider == "openai_responses" else setting.bridge_url
    destination_host = (urlsplit(destination or "").hostname or "").lower()
    allowed_hosts = {
        host.strip().lower() for host in get_settings().ai_allowed_provider_hosts if host.strip()
    }
    if destination_host and destination_host not in allowed_hosts:
        raise APIError(
            422,
            "provider_host_not_allowed",
            "The selected provider URL host is not in AI_ALLOWED_PROVIDER_HOSTS",
        )


def _skill_out(skill: AISkill) -> AISkillOut:
    return AISkillOut.model_validate(skill)


def _draft_out(draft: AIDraft | None) -> AIDraftOut | None:
    if draft is None:
        return None
    return AIDraftOut(
        id=draft.id,
        job_id=draft.job_id,
        source_tweet_id=draft.source_tweet_id,
        title=draft.title,
        content=draft.content,
        excerpt=draft.excerpt,
        status=draft.status,
        metadata=draft.draft_metadata,
        revision=draft.revision,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _job_out(
    job: AIGenerationJob,
    source_x_tweet_id: str | None,
    source_text: str | None = None,
    source_username: str | None = None,
    *,
    detail: bool = False,
) -> AIJobOut | AIJobDetail:
    common = {
        "id": job.id,
        "source_tweet_id": job.source_tweet_id,
        "source_x_tweet_id": source_x_tweet_id,
        "source_text": source_text,
        "source_username": source_username,
        "skill_id": job.skill_id,
        "skill_ids": job.skill_ids or [],
        "skill_snapshot": job.skill_snapshot or [],
        "idempotency_key": job.idempotency_key,
        "status": job.status,
        "provider": job.provider,
        "model": job.model_name,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "next_attempt_at": job.next_attempt_at,
        "manual": job.manual,
        "last_error": job.last_error,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "draft": _draft_out(job.draft),
    }
    if detail:
        return AIJobDetail(
            **common,
            request_snapshot=job.request_snapshot,
            response_snapshot=job.response_snapshot,
            prompt_hash=job.prompt_hash,
            source_text_hash=job.source_text_hash,
        )
    return AIJobOut(**common)


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
        provider=setting.provider,
        model=setting.model_name,
        base_url=setting.base_url,
        bridge_url=setting.bridge_url,
        prompt_template=setting.prompt_template,
        language=setting.language,
        tone=setting.tone,
        require_review=setting.require_review,
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
    setting = await get_ai_setting(db, for_update=True)
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
    if setting.provider == "codex_bridge" and setting.enabled and not setting.bridge_url:
        raise APIError(
            422,
            "bridge_url_required",
            "bridge_url is required when the codex_bridge provider is enabled",
        )
    _ensure_provider_host_allowed(setting)
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
    skill = await db.get(AISkill, skill_id, with_for_update=True)
    if skill is None:
        raise APIError(404, "skill_not_found", "AI skill was not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(skill, key, value)
    skill.version += 1
    if payload.is_active is False:
        setting = await get_ai_setting(db, for_update=True)
        setting.default_skill_ids = [
            selected for selected in (setting.default_skill_ids or []) if selected != skill_id
        ]
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "skill_name_exists", "A skill with this name already exists") from None
    await db.refresh(skill)
    return _skill_out(skill)


@router.delete("/skills/{skill_id}", response_model=MessageResponse)
async def delete_ai_skill(skill_id: int, db: DbSession, _: CurrentAdmin) -> MessageResponse:
    skill = await db.get(AISkill, skill_id, with_for_update=True)
    if skill is None:
        raise APIError(404, "skill_not_found", "AI skill was not found")
    skill.is_active = False
    skill.version += 1
    setting = await get_ai_setting(db, for_update=True)
    setting.default_skill_ids = [
        selected for selected in (setting.default_skill_ids or []) if selected != skill_id
    ]
    await db.commit()
    return MessageResponse(message="AI skill deactivated")


@router.get("/jobs", response_model=Page[AIJobOut])
async def list_ai_jobs(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    job_status: str | None = Query(default=None, alias="status", max_length=24),
    provider: str | None = Query(default=None, max_length=32),
    manual: bool | None = None,
    source_tweet_id: int | None = None,
) -> Page[AIJobOut]:
    conditions = []
    if job_status:
        conditions.append(AIGenerationJob.status == job_status)
    if provider:
        conditions.append(AIGenerationJob.provider == provider)
    if manual is not None:
        conditions.append(AIGenerationJob.manual == manual)
    if source_tweet_id is not None:
        conditions.append(AIGenerationJob.source_tweet_id == source_tweet_id)
    total = int(await db.scalar(select(func.count(AIGenerationJob.id)).where(*conditions)) or 0)
    rows = (
        await db.execute(
            select(AIGenerationJob, Tweet.tweet_id, Tweet.text, MonitoredUser.username)
            .join(Tweet, Tweet.id == AIGenerationJob.source_tweet_id)
            .join(MonitoredUser, MonitoredUser.id == Tweet.monitored_user_id)
            .options(selectinload(AIGenerationJob.draft))
            .where(*conditions)
            .order_by(AIGenerationJob.created_at.desc(), AIGenerationJob.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page(
        items=[
            _job_out(job, tweet_id, source_text, username)
            for job, tweet_id, source_text, username in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/jobs/{job_id}", response_model=AIJobDetail)
async def get_ai_job(job_id: int, db: DbSession, _: CurrentAdmin) -> AIJobDetail:
    row = (
        await db.execute(
            select(AIGenerationJob, Tweet.tweet_id, Tweet.text, MonitoredUser.username)
            .join(Tweet, Tweet.id == AIGenerationJob.source_tweet_id)
            .join(MonitoredUser, MonitoredUser.id == Tweet.monitored_user_id)
            .options(selectinload(AIGenerationJob.draft))
            .where(AIGenerationJob.id == job_id)
        )
    ).one_or_none()
    if row is None:
        raise APIError(404, "ai_job_not_found", "AI generation job was not found")
    return _job_out(row[0], row[1], row[2], row[3], detail=True)  # type: ignore[return-value]


@router.post("/jobs/{job_id}/retry", response_model=AIJobOut)
async def retry_ai_job(job_id: int, db: DbSession, _: CurrentAdmin) -> AIJobOut:
    job = await db.scalar(
        select(AIGenerationJob)
        .where(AIGenerationJob.id == job_id)
        .options(selectinload(AIGenerationJob.draft))
        .with_for_update()
    )
    if job is None:
        raise APIError(404, "ai_job_not_found", "AI generation job was not found")
    if job.status not in {"failed", "retry_wait", "cancelled"}:
        raise APIError(409, "ai_job_not_retryable", "Only failed or cancelled jobs can be retried")
    job.status = "queued"
    job.attempts = 0
    job.next_attempt_at = datetime.now(UTC)
    job.claim_token = None
    job.claimed_by = None
    job.last_error = None
    job.completed_at = None
    await db.commit()
    await db.refresh(job)
    source = (
        await db.execute(
            select(Tweet.tweet_id, Tweet.text, MonitoredUser.username)
            .join(MonitoredUser, MonitoredUser.id == Tweet.monitored_user_id)
            .where(Tweet.id == job.source_tweet_id)
        )
    ).one_or_none()
    return _job_out(
        job,
        source[0] if source else None,
        source[1] if source else None,
        source[2] if source else None,
    )  # type: ignore[return-value]


@tweets_router.post(
    "/{tweet_id}/generate",
    response_model=AIJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_from_tweet(
    tweet_id: str,
    payload: ManualGenerateRequest,
    db: DbSession,
    _: CurrentAdmin,
) -> AIJobOut:
    tweet = await db.scalar(select(Tweet).where(Tweet.tweet_id == tweet_id))
    if tweet is None:
        raise APIError(404, "tweet_not_found", "Tweet was not found")
    setting = await get_ai_setting(db)
    requested_ids = (
        payload.skill_ids if payload.skill_ids is not None else setting.default_skill_ids or []
    )
    skills = await resolve_active_skills(db, requested_ids)
    if [skill.id for skill in skills] != requested_ids:
        raise APIError(422, "invalid_skill_ids", "All selected skills must exist and be active")
    try:
        job, _created = await create_manual_job(
            db,
            tweet=tweet,
            skills=skills,
            idempotency_key=payload.idempotency_key,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        if str(exc) == "ai_disabled":
            raise APIError(409, "ai_disabled", "AI generation is disabled") from None
        raise
    except IntegrityError:
        await db.rollback()
        if not payload.idempotency_key:
            raise
        key = f"manual:{tweet.id}:client:{payload.idempotency_key}"
        job = await db.scalar(select(AIGenerationJob).where(AIGenerationJob.idempotency_key == key))
        if job is None:
            raise
    job = await db.scalar(
        select(AIGenerationJob)
        .where(AIGenerationJob.id == job.id)
        .options(selectinload(AIGenerationJob.draft))
    )
    if job is None:
        raise APIError(500, "ai_job_missing", "Generation job could not be loaded")
    username = await db.scalar(
        select(MonitoredUser.username).where(MonitoredUser.id == tweet.monitored_user_id)
    )
    return _job_out(job, tweet.tweet_id, tweet.text, username)  # type: ignore[return-value]


@router.patch("/drafts/{draft_id}", response_model=AIDraftOut)
async def patch_ai_draft(
    draft_id: int, payload: AIDraftPatch, db: DbSession, _: CurrentAdmin
) -> AIDraftOut:
    draft = await db.scalar(select(AIDraft).where(AIDraft.id == draft_id).with_for_update())
    if draft is None:
        raise APIError(404, "ai_draft_not_found", "AI draft was not found")
    if draft.revision != payload.revision:
        raise APIError(
            409,
            "draft_revision_conflict",
            "The draft was updated by another request",
            {"current_revision": draft.revision},
        )
    changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
    for key, value in changes.items():
        setattr(draft, "draft_metadata" if key == "metadata" else key, value)
    draft.revision += 1
    await db.commit()
    await db.refresh(draft)
    result = _draft_out(draft)
    if result is None:  # pragma: no cover - kept for static type narrowing
        raise APIError(500, "ai_draft_missing", "Draft could not be loaded")
    return result
