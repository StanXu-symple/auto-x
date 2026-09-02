from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import (
    AIFeature,
    AIGenerationJob,
    AISetting,
    AISkill,
    AIUserProfile,
    AIUserSkillBinding,
)
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.services.ai_defaults import DEFAULT_AI_FEATURE_CODE, DEFAULT_AI_MODEL


async def get_ai_setting(session: AsyncSession, *, for_update: bool = False) -> AISetting:
    statement = select(AISetting).where(AISetting.id == 1)
    if for_update:
        statement = statement.with_for_update()
    setting = await session.scalar(statement)
    if setting is not None:
        return setting
    setting = AISetting(
        id=1,
        enabled=False,
        auto_generate=True,
        provider="openai_responses",
        model_name=DEFAULT_AI_MODEL,
        base_url="https://api.openai.com/v1",
        bridge_url=None,
        prompt_template=None,
        language="zh-CN",
        tone="专业自然",
        require_review=True,
        reasoning_effort="medium",
        default_skill_ids=[],
        max_attempts=3,
        max_output_tokens=2500,
        request_timeout_seconds=60,
    )
    session.add(setting)
    await session.flush()
    return setting


async def resolve_active_skills(session: AsyncSession, skill_ids: list[int]) -> list[AISkill]:
    if not skill_ids:
        return []
    skills = list(
        await session.scalars(
            select(AISkill).where(AISkill.id.in_(skill_ids), AISkill.is_active.is_(True))
        )
    )
    by_id = {skill.id: skill for skill in skills}
    return [by_id[skill_id] for skill_id in skill_ids if skill_id in by_id]


async def get_ai_feature(session: AsyncSession, feature_code: str) -> AIFeature:
    feature = await session.scalar(
        select(AIFeature).where(AIFeature.code == feature_code, AIFeature.is_active.is_(True))
    )
    if feature is None:
        raise ValueError("ai_feature_not_found")
    return feature


async def resolve_context_skills(
    session: AsyncSession,
    *,
    monitored_user_id: int,
    feature: AIFeature,
    fallback_skill_ids: list[int],
    override_skill_ids: list[int] | None = None,
) -> tuple[list[AISkill], str]:
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


async def build_author_context(
    session: AsyncSession, *, monitored_user_id: int, source_tweet_id: int
) -> dict:
    user = await session.get(MonitoredUser, monitored_user_id)
    if user is None:
        raise ValueError("monitored_user_not_found")
    profile = await session.get(AIUserProfile, monitored_user_id)
    recent = list(
        await session.scalars(
            select(Tweet)
            .where(Tweet.monitored_user_id == monitored_user_id, Tweet.id != source_tweet_id)
            .order_by(Tweet.posted_at.desc(), Tweet.id.desc())
            .limit(20)
        )
    )
    return {
        "author": {
            "monitored_user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "x_user_id": user.x_user_id,
        },
        "persisted_profile": {
            "identity_summary": profile.identity_summary if profile else "",
            "focus_summary": profile.focus_summary if profile else "",
            "relationship_summary": profile.relationship_summary if profile else "",
            "recurring_topics": profile.recurring_topics if profile else [],
            "evidence": profile.evidence if profile else [],
            "confidence": profile.confidence if profile else 0,
            "version": profile.version if profile else 0,
        },
        "recent_dynamics": [
            {
                "tweet_id": tweet.tweet_id,
                "posted_at": tweet.posted_at.isoformat(),
                "text": tweet.text,
                "metrics": {
                    "likes": tweet.like_count,
                    "retweets": tweet.retweet_count,
                    "replies": tweet.reply_count,
                },
            }
            for tweet in recent
        ],
    }


def _skill_snapshot(skill: AISkill) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "instructions": skill.instructions,
        "output_schema": skill.output_schema,
        "version": skill.version,
        "remote_skill_id": skill.remote_skill_id,
        "remote_skill_version": skill.remote_skill_version,
    }


def _request_snapshot(
    setting: AISetting,
    skills: list[AISkill],
    tweet: Tweet,
    *,
    feature: AIFeature,
    skill_resolution: str,
    author_context: dict,
) -> dict:
    return {
        "config": {
            "provider": setting.provider,
            "model": setting.model_name,
            "base_url": setting.base_url,
            "bridge_url": setting.bridge_url,
            "prompt_template": setting.prompt_template,
            "language": setting.language,
            "tone": setting.tone,
            "require_review": setting.require_review,
            "reasoning_effort": setting.reasoning_effort,
            "max_output_tokens": setting.max_output_tokens,
            "request_timeout_seconds": setting.request_timeout_seconds,
        },
        "skills": [_skill_snapshot(skill) for skill in skills],
        "feature": {
            "id": feature.id,
            "code": feature.code,
            "name": feature.name,
            "description": feature.description,
            "base_prompt": feature.base_prompt,
        },
        "skill_resolution": skill_resolution,
        "author_context": author_context,
        "source": {
            "database_id": tweet.id,
            "tweet_id": tweet.tweet_id,
            "author_id": tweet.author_id,
            "text": tweet.text,
            "lang": tweet.lang,
            "posted_at": tweet.posted_at.isoformat(),
        },
    }


async def enqueue_auto_jobs(session: AsyncSession, tweet_ids: list[int]) -> int:
    """Create at most one automatic generation job for each persisted tweet."""
    if not tweet_ids:
        return 0
    setting = await get_ai_setting(session)
    if not setting.enabled or not setting.auto_generate:
        return 0
    feature = await get_ai_feature(session, DEFAULT_AI_FEATURE_CODE)
    unique_tweet_ids = sorted(set(tweet_ids))
    tweets = list(await session.scalars(select(Tweet).where(Tweet.id.in_(unique_tweet_ids))))
    tweets_by_id = {tweet.id: tweet for tweet in tweets}
    unique_tweet_ids = [tweet_id for tweet_id in unique_tweet_ids if tweet_id in tweets_by_id]
    if not unique_tweet_ids:
        return 0
    keys = [f"auto:{tweet_id}" for tweet_id in unique_tweet_ids]
    existing = set(
        await session.scalars(
            select(AIGenerationJob.idempotency_key).where(AIGenerationJob.idempotency_key.in_(keys))
        )
    )
    now = datetime.now(UTC)
    values = []
    for tweet_id in unique_tweet_ids:
        if tweet_id not in tweets_by_id:
            continue
        tweet = tweets_by_id[tweet_id]
        skills, resolution = await resolve_context_skills(
            session,
            monitored_user_id=tweet.monitored_user_id,
            feature=feature,
            fallback_skill_ids=setting.default_skill_ids or [],
        )
        skill_ids = [skill.id for skill in skills]
        author_context = await build_author_context(
            session,
            monitored_user_id=tweet.monitored_user_id,
            source_tweet_id=tweet.id,
        )
        values.append({
            "source_tweet_id": tweet_id,
            "feature_code": feature.code,
            "skill_id": skill_ids[0] if skill_ids else None,
            "skill_ids": skill_ids,
            "skill_snapshot": [_skill_snapshot(skill) for skill in skills],
            "idempotency_key": f"auto:{tweet_id}",
            "status": "queued",
            "provider": setting.provider,
            "model": setting.model_name,
            "attempts": 0,
            "max_attempts": setting.max_attempts,
            "next_attempt_at": now,
            "manual": False,
            "request_snapshot": _request_snapshot(
                setting,
                skills,
                tweet,
                feature=feature,
                skill_resolution=resolution,
                author_context=author_context,
            ),
            "source_text_hash": hashlib.sha256(
                tweet.text.encode("utf-8")
            ).hexdigest(),
            "created_at": now,
            "updated_at": now,
        })
    statement = mysql_insert(AIGenerationJob).values(values)
    statement = statement.on_duplicate_key_update(
        idempotency_key=statement.inserted.idempotency_key
    )
    await session.execute(statement)
    return len(set(keys) - existing)


async def enqueue_jobs_for_x_tweet_ids(session: AsyncSession, x_tweet_ids: list[str]) -> int:
    if not x_tweet_ids:
        return 0
    database_ids = list(
        await session.scalars(select(Tweet.id).where(Tweet.tweet_id.in_(set(x_tweet_ids))))
    )
    return await enqueue_auto_jobs(session, database_ids)


async def create_manual_job(
    session: AsyncSession,
    *,
    tweet: Tweet,
    feature: AIFeature,
    skills: list[AISkill],
    skill_resolution: str,
    idempotency_key: str | None,
) -> tuple[AIGenerationJob, bool]:
    setting = await get_ai_setting(session)
    if not setting.enabled:
        raise ValueError("ai_disabled")
    stable_key = (
        f"manual:{tweet.id}:client:{idempotency_key}"
        if idempotency_key
        else f"manual:{tweet.id}:{'-'.join(str(skill.id) for skill in skills) or '0'}:"
        f"{uuid.uuid4().hex}"
    )
    existing = await session.scalar(
        select(AIGenerationJob).where(AIGenerationJob.idempotency_key == stable_key)
    )
    if existing is not None:
        return existing, False
    now = datetime.now(UTC)
    job = AIGenerationJob(
        source_tweet_id=tweet.id,
        feature_code=feature.code,
        skill_id=skills[0].id if skills else None,
        skill_ids=[skill.id for skill in skills],
        skill_snapshot=[_skill_snapshot(skill) for skill in skills],
        idempotency_key=stable_key,
        status="queued",
        provider=setting.provider,
        model_name=setting.model_name,
        attempts=0,
        max_attempts=setting.max_attempts,
        next_attempt_at=now,
        manual=True,
        request_snapshot=_request_snapshot(
            setting,
            skills,
            tweet,
            feature=feature,
            skill_resolution=skill_resolution,
            author_context=await build_author_context(
                session,
                monitored_user_id=tweet.monitored_user_id,
                source_tweet_id=tweet.id,
            ),
        ),
        source_text_hash=hashlib.sha256(tweet.text.encode("utf-8")).hexdigest(),
    )
    session.add(job)
    await session.flush()
    return job, True
