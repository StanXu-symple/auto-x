from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import as_utc
from app.models.ai import (
    AIFeature,
    AIGenerationJob,
    AIListenTask,
    AIListenTaskEvent,
    AIListenTaskSkill,
    AIListenTaskSubscription,
    AISetting,
    AISkill,
    AIUserProfile,
    AIUserSkillBinding,
)
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.services.ai_defaults import DEFAULT_AI_FEATURE_CODE, DEFAULT_AI_MODEL

logger = logging.getLogger(__name__)


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
        auto_trigger_mode="listening_tasks",
        provider="openai_responses",
        model_name=DEFAULT_AI_MODEL,
        base_url="https://api.openai.com/v1",
        bridge_url=None,
        prompt_template=None,
        language="zh-CN",
        tone="专业自然",
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
    """Route newly persisted tweets to exactly one automatic trigger mode.

    The singleton settings lock serializes this decision with mode changes. Polling
    calls this inside the same transaction that inserts the source tweets.
    """
    if not tweet_ids:
        return 0
    setting = await get_ai_setting(session, for_update=True)
    if not setting.enabled or not setting.auto_generate:
        return 0
    if setting.auto_trigger_mode == "listening_tasks":
        return await enqueue_listening_jobs(session, tweet_ids, setting=setting)
    if setting.auto_trigger_mode != "legacy_all":
        logger.error("Unknown AI auto trigger mode", extra={"mode": setting.auto_trigger_mode})
        return 0
    feature = await get_ai_feature(session, DEFAULT_AI_FEATURE_CODE)
    unique_tweet_ids = sorted(set(tweet_ids))
    tweets = list(await session.scalars(select(Tweet).where(Tweet.id.in_(unique_tweet_ids))))
    tweets_by_id = {tweet.id: tweet for tweet in tweets}
    unique_tweet_ids = [tweet_id for tweet_id in unique_tweet_ids if tweet_id in tweets_by_id]
    if not unique_tweet_ids:
        return 0
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
            "trigger_type": "legacy_auto",
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
    if not values:
        return 0
    statement = postgres_insert(AIGenerationJob).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=[AIGenerationJob.idempotency_key])
    inserted = await session.scalars(statement.returning(AIGenerationJob.id))
    return len(list(inserted))


async def enqueue_listening_jobs(
    session: AsyncSession,
    tweet_ids: list[int],
    *,
    task_id: int | None = None,
    backfill_window: tuple[datetime, datetime] | None = None,
    backfill_snapshot: dict | None = None,
    setting: AISetting | None = None,
) -> int:
    """Match tweets against active listening tasks and insert immutable job snapshots.

    A backfill has an explicit time window and bypasses the realtime lower bound.
    Callers must commit the surrounding ingestion or backfill transaction.
    """
    if not tweet_ids:
        return 0
    if setting is None:
        setting = await get_ai_setting(session, for_update=True)
    if (
        not setting.enabled
        or not setting.auto_generate
        or setting.auto_trigger_mode != "listening_tasks"
    ):
        return 0
    unique_ids = sorted(set(tweet_ids))
    tweets = list(await session.scalars(select(Tweet).where(Tweet.id.in_(unique_ids))))
    if not tweets:
        return 0
    task_query = select(AIListenTask).where(AIListenTask.desired_state == "enabled")
    if task_id is not None:
        task_query = task_query.where(AIListenTask.id == task_id)
    # State transitions take this same row lock. A pause committed before this
    # transaction prevents enqueue; one committed afterward takes effect next poll.
    tasks = list(await session.scalars(task_query.order_by(AIListenTask.id).with_for_update()))
    if not tasks:
        return 0
    user_ids = {tweet.monitored_user_id for tweet in tweets}
    users = {
        user.id: user
        for user in await session.scalars(
            select(MonitoredUser).where(MonitoredUser.id.in_(user_ids))
        )
    }
    now = datetime.now(UTC)
    snapshots_by_tweet: dict[int, dict] = {}
    inserted_count = 0
    for task in tasks:
        if task.queue_hold_reason:
            continue
        subscriptions = list(
            await session.scalars(
                select(AIListenTaskSubscription).where(
                    AIListenTaskSubscription.task_id == task.id
                )
            )
        )
        subscription_by_user = {item.monitored_user_id: item for item in subscriptions}
        selected = list(
            await session.scalars(
                select(AIListenTaskSkill)
                .where(AIListenTaskSkill.task_id == task.id)
                .order_by(AIListenTaskSkill.priority, AIListenTaskSkill.skill_id)
            )
        )
        current_ids = [item.skill_id for item in selected]
        current_skills = await resolve_active_skills(session, current_ids)
        if not current_ids or len(current_skills) != len(current_ids):
            task.queue_hold_reason = "skill_invalidated"
            logger.warning(
                "AI listening task skill selection is invalid", extra={"task_id": task.id}
            )
            continue
        selected_ids = (
            [int(value) for value in backfill_snapshot.get("skill_ids") or []]
            if backfill_snapshot is not None
            else current_ids
        )
        skill_snapshots = (
            list(backfill_snapshot.get("skills") or [])
            if backfill_snapshot is not None
            else [_skill_snapshot(skill) for skill in current_skills]
        )
        if not selected_ids or len(skill_snapshots) != len(selected_ids):
            continue
        task_mode = (
            str(backfill_snapshot.get("listen_mode") or task.listen_mode)
            if backfill_snapshot is not None
            else task.listen_mode
        )
        all_users = (
            bool(backfill_snapshot.get("all_monitored_users"))
            if backfill_snapshot is not None
            else task.all_monitored_users
        )
        selected_accounts = (
            {int(value) for value in backfill_snapshot.get("monitored_user_ids") or []}
            if backfill_snapshot is not None
            else set(subscription_by_user)
        )
        feature_code = (
            str(backfill_snapshot.get("feature_code") or task.feature_code)
            if backfill_snapshot is not None
            else task.feature_code
        )
        if backfill_snapshot is not None and backfill_snapshot.get(
            "exclude_legacy_generated", True
        ):
            legacy_keys = {f"auto:{tweet.id}" for tweet in tweets}
            legacy_generated = set(
                await session.scalars(
                    select(AIGenerationJob.idempotency_key).where(
                        AIGenerationJob.idempotency_key.in_(legacy_keys)
                    )
                )
            )
        else:
            legacy_generated = set()
        try:
            feature = await get_ai_feature(session, feature_code)
        except ValueError:
            logger.warning("AI listening task feature is inactive", extra={"task_id": task.id})
            continue
        values = []
        for tweet in tweets:
            user = users.get(tweet.monitored_user_id)
            if user is None or not user.is_active:
                continue
            if task_mode != "all" and task_mode != tweet.tweet_type:
                continue
            if f"auto:{tweet.id}" in legacy_generated:
                continue
            # A requested backfill scans posts that are already stored, using its
            # frozen account/type selection. Current collection switches only
            # govern which new posts enter the realtime path.
            if backfill_snapshot is None:
                if tweet.tweet_type == "reply" and not user.include_replies:
                    continue
                if tweet.tweet_type == "retweet" and not user.include_retweets:
                    continue
            subscription = subscription_by_user.get(tweet.monitored_user_id)
            if backfill_snapshot is not None and tweet.monitored_user_id not in selected_accounts:
                continue
            if backfill_snapshot is None and not all_users and subscription is None:
                continue
            if backfill_window is not None:
                posted_at = as_utc(tweet.posted_at)
                if not (as_utc(backfill_window[0]) <= posted_at < as_utc(backfill_window[1])):
                    continue
            else:
                effective_from = task.effective_from or task.activated_at
                if all_users:
                    # New monitored accounts do not backdate the task to their old posts.
                    if user.created_at and (
                        effective_from is None or as_utc(user.created_at) > as_utc(effective_from)
                    ):
                        effective_from = user.created_at
                elif subscription is not None and subscription.effective_from:
                    if effective_from is None or as_utc(
                        subscription.effective_from
                    ) > as_utc(effective_from):
                        effective_from = subscription.effective_from
                in_initial_window = (
                    task.initial_backfill_from is not None
                    and task.initial_backfill_to is not None
                    and as_utc(task.initial_backfill_from)
                    <= as_utc(tweet.posted_at)
                    < as_utc(task.initial_backfill_to)
                    and (
                        (
                            all_users
                            and user.created_at is not None
                            and as_utc(user.created_at) <= as_utc(task.initial_backfill_to)
                        )
                        or (
                            subscription is not None
                            and subscription.effective_from is not None
                            and as_utc(subscription.effective_from)
                            <= as_utc(task.initial_backfill_to)
                        )
                    )
                )
                if not in_initial_window and (
                    effective_from is None or as_utc(tweet.posted_at) < as_utc(effective_from)
                ):
                    continue
            author_context = snapshots_by_tweet.get(tweet.id)
            if author_context is None:
                author_context = await build_author_context(
                    session,
                    monitored_user_id=tweet.monitored_user_id,
                    source_tweet_id=tweet.id,
                )
                snapshots_by_tweet[tweet.id] = author_context
            request_snapshot = _request_snapshot(
                setting,
                current_skills,
                tweet,
                feature=feature,
                skill_resolution="listening_task",
                author_context=author_context,
            )
            request_snapshot["skills"] = skill_snapshots
            config = request_snapshot["config"]
            overrides = backfill_snapshot if backfill_snapshot is not None else task
            for field, config_key in (
                ("language_override", "language"),
                ("tone_override", "tone"),
                ("max_output_tokens_override", "max_output_tokens"),
            ):
                value = (
                    overrides.get(field)
                    if isinstance(overrides, dict)
                    else getattr(overrides, field)
                )
                if value is not None:
                    config[config_key] = value
            config_version = (
                int(backfill_snapshot.get("config_version") or task.config_version)
                if backfill_snapshot is not None
                else task.config_version
            )
            max_attempts_override = (
                backfill_snapshot.get("max_attempts_override")
                if backfill_snapshot is not None
                else task.max_attempts_override
            )
            values.append({
                "source_tweet_id": tweet.id,
                "listen_task_id": task.id,
                "trigger_type": "listen_task",
                "task_config_version": config_version,
                "task_snapshot": {
                    "task_id": task.id,
                    "name": task.name,
                    "listen_mode": task_mode,
                    "all_monitored_users": all_users,
                    "skill_ids": selected_ids,
                    "config_version": config_version,
                },
                "feature_code": feature.code,
                "skill_id": selected_ids[0],
                "skill_ids": selected_ids,
                "skill_snapshot": skill_snapshots,
                "idempotency_key": f"listen:{task.id}:tweet:{tweet.id}",
                "status": "queued",
                "provider": setting.provider,
                "model": setting.model_name,
                "attempts": 0,
                "lifetime_attempts": 0,
                "max_attempts": max_attempts_override or setting.max_attempts,
                "next_attempt_at": now,
                "manual": False,
                "request_snapshot": request_snapshot,
                "source_text_hash": hashlib.sha256(tweet.text.encode("utf-8")).hexdigest(),
                "created_at": now,
                "updated_at": now,
            })
        if not values:
            continue
        statement = postgres_insert(AIGenerationJob).values(values)
        statement = statement.on_conflict_do_nothing(
            index_elements=[AIGenerationJob.idempotency_key]
        ).returning(AIGenerationJob.id)
        inserted_for_task = len(list(await session.scalars(statement)))
        inserted_count += inserted_for_task
        if inserted_for_task:
            session.add(
                AIListenTaskEvent(
                    task_id=task.id,
                    event_type="jobs_enqueued",
                    summary=f"Queued {inserted_for_task} AI generation job(s)",
                    details={
                        "count": inserted_for_task,
                        "source": "backfill" if backfill_window is not None else "realtime",
                    },
                )
            )
    return inserted_count


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
        trigger_type="manual",
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
