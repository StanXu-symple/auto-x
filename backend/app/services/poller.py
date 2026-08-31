from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.time import as_utc
from app.models.monitored_user import MonitoredUser
from app.models.polling_log import PollingLog
from app.models.tweet import Tweet
from app.services.ai_jobs import enqueue_jobs_for_x_tweet_ids
from app.services.metrics import POLL_DURATION, POLL_RUNS, TWEETS_INGESTED
from app.services.settings_service import effective_interval, get_polling_settings
from app.services.x_client import TweetBatch, XAPIError, XClient, XRateLimitError, XUser

logger = logging.getLogger(__name__)

GLOBAL_X_GATE_KEY = "xsentinel:x-api:gate"
TWEET_INSERT_CHUNK_SIZE = 150

RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""
RENEW_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
else
  return 0
end
"""


class LockLostError(RuntimeError):
    pass


class IdentityConflictError(RuntimeError):
    pass


@dataclass(slots=True)
class PollClaim:
    user_id: int
    log_id: int
    generation: int
    trigger: str
    manual_token: str | None
    username: str
    x_user_id: str | None
    since_id: str | None
    pagination_token: str | None
    pagination_since_id: str | None
    pagination_newest_id: str | None
    include_replies: bool
    include_retweets: bool


@dataclass(slots=True)
class CommitResult:
    status: str
    tweets_inserted: int = 0
    applied: bool = False


@dataclass(slots=True)
class PaginationState:
    last_tweet_id: str | None
    token: str | None
    since_id: str | None
    newest_id: str | None
    status: str


def calculate_pagination_state(claim: PollClaim, batch: TweetBatch) -> PaginationState:
    """Advance the high-water mark only after the pagination chain is drained."""
    checkpoint_newest = claim.pagination_newest_id or batch.newest_id
    if batch.next_token:
        return PaginationState(
            last_tweet_id=claim.since_id,
            token=batch.next_token,
            since_id=(
                claim.pagination_since_id if claim.pagination_token is not None else claim.since_id
            ),
            newest_id=checkpoint_newest,
            status="backfilling",
        )
    return PaginationState(
        last_tweet_id=checkpoint_newest or claim.since_id,
        token=None,
        since_id=None,
        newest_id=None,
        status="idle",
    )


def parse_x_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return datetime.now(UTC)


def earlier_deadline(existing: datetime | None, proposed: datetime) -> datetime:
    if existing is None:
        return proposed
    return min(as_utc(existing), proposed)


class PollingService:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        x_client: XClient,
        settings: Settings,
        worker_id: str,
    ) -> None:
        self.session_factory = session_factory
        self.redis = redis
        self.x_client = x_client
        self.settings = settings
        self.worker_id = worker_id

    async def poll_user(self, user_id: int) -> bool:
        lock_key = f"xsentinel:poll:lock:{user_id}"
        lock_token = uuid.uuid4().hex
        try:
            acquired = await self.redis.set(
                lock_key,
                lock_token,
                nx=True,
                ex=self.settings.worker_lock_ttl_seconds,
            )
        except Exception:
            logger.exception("Redis lock acquisition failed", extra={"monitored_user_id": user_id})
            return False
        if not acquired:
            logger.debug(
                "Poll skipped because another worker owns the lock", extra={"user_id": user_id}
            )
            return False

        lost_lock = asyncio.Event()
        renew_task = asyncio.create_task(self._renew_lock(lock_key, lock_token, lost_lock))
        started_perf = time.perf_counter()
        claim: PollClaim | None = None
        try:
            if await self._global_gate_active():
                return False
            claim = await self._claim_user(user_id)
            if claim is None:
                return False

            resolved_user: XUser | None = None
            x_user_id = claim.x_user_id
            if not x_user_id:
                resolved_user = await self.x_client.lookup_user(claim.username)
                x_user_id = resolved_user.id
                await self._ensure_identity_available(claim.user_id, x_user_id)

            since_id = (
                claim.pagination_since_id if claim.pagination_token is not None else claim.since_id
            )
            batch = await self.x_client.get_user_tweets(
                x_user_id,
                since_id=since_id,
                initial_pagination_token=claim.pagination_token,
                include_replies=claim.include_replies,
                include_retweets=claim.include_retweets,
            )
            await self._assert_lock(lock_key, lock_token, lost_lock)
            result = await self._commit_success(
                claim,
                x_user_id=x_user_id,
                resolved_user=resolved_user,
                batch=batch,
                started_perf=started_perf,
                lock_key=lock_key,
                lock_token=lock_token,
                lost_lock=lost_lock,
            )
            self._observe_result(result.status, claim.trigger, started_perf)
            if result.tweets_inserted:
                TWEETS_INGESTED.inc(result.tweets_inserted)
            if result.applied:
                logger.info(
                    "Poll batch completed",
                    extra={
                        "monitored_user_id": user_id,
                        "generation": claim.generation,
                        "status": result.status,
                        "tweets_fetched": batch.result_count,
                        "tweets_inserted": result.tweets_inserted,
                        "has_next_page": batch.next_token is not None,
                        "trigger": claim.trigger,
                    },
                )
            return result.applied
        except XRateLimitError as exc:
            reset_at = max(
                exc.reset_at + timedelta(seconds=5),
                datetime.now(UTC) + timedelta(seconds=15),
            )
            await self._set_global_gate("rate_limited", reset_at, 429)
            return await self._handle_failure(
                claim,
                status="rate_limited",
                message=str(exc),
                http_status=429,
                proposed_deadline=reset_at,
                rate_limit_reset_at=exc.reset_at,
                started_perf=started_perf,
                lock_key=lock_key,
                lock_token=lock_token,
                lost_lock=lost_lock,
            )
        except IdentityConflictError as exc:
            return await self._handle_failure(
                claim,
                status="identity_conflict",
                message=str(exc),
                http_status=409,
                proposed_deadline=self._long_backoff(),
                rate_limit_reset_at=None,
                started_perf=started_perf,
                lock_key=lock_key,
                lock_token=lock_token,
                lost_lock=lost_lock,
            )
        except IntegrityError as exc:
            return await self._handle_failure(
                claim,
                status="identity_conflict",
                message=f"X identity conflicts with another monitored user: {exc.orig}",
                http_status=409,
                proposed_deadline=self._long_backoff(),
                rate_limit_reset_at=None,
                started_perf=started_perf,
                lock_key=lock_key,
                lock_token=lock_token,
                lost_lock=lost_lock,
            )
        except XAPIError as exc:
            if exc.status_code in {401, 403}:
                deadline = datetime.now(UTC) + timedelta(seconds=self.settings.x_auth_gate_seconds)
                await self._set_global_gate("authentication", deadline, exc.status_code)
            elif exc.status_code == 404:
                deadline = self._long_backoff()
            else:
                deadline = None
            return await self._handle_failure(
                claim,
                status="error",
                message=str(exc),
                http_status=exc.status_code,
                proposed_deadline=deadline,
                rate_limit_reset_at=None,
                started_perf=started_perf,
                lock_key=lock_key,
                lock_token=lock_token,
                lost_lock=lost_lock,
            )
        except LockLostError as exc:
            if claim is not None:
                await self._finalize_log_only(
                    claim.log_id,
                    status="lock_lost",
                    started_perf=started_perf,
                    error_message=str(exc),
                )
                self._observe_result("lock_lost", claim.trigger, started_perf)
            return False
        except asyncio.CancelledError:
            if claim is not None:
                await self._finalize_log_only(
                    claim.log_id,
                    status="cancelled",
                    started_perf=started_perf,
                    error_message="Polling task was cancelled",
                )
            raise
        except Exception as exc:
            logger.exception("Unexpected poll failure", extra={"monitored_user_id": user_id})
            return await self._handle_failure(
                claim,
                status="error",
                message=f"Unexpected polling error: {exc}",
                http_status=None,
                proposed_deadline=None,
                rate_limit_reset_at=None,
                started_perf=started_perf,
                lock_key=lock_key,
                lock_token=lock_token,
                lost_lock=lost_lock,
            )
        finally:
            renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await renew_task
            try:
                await self.redis.eval(RELEASE_LOCK_SCRIPT, 1, lock_key, lock_token)
            except Exception:
                logger.exception("Redis lock release failed", extra={"monitored_user_id": user_id})

    async def _claim_user(self, user_id: int) -> PollClaim | None:
        async with self.session_factory() as session, session.begin():
            user = await session.get(MonitoredUser, user_id, with_for_update=True)
            if user is None or not user.is_active:
                return None
            user.poll_generation += 1
            user.status = "polling"
            user.next_poll_at = None
            user.last_error = None
            trigger = "manual" if user.manual_poll_token else "scheduled"
            log_row = PollingLog(
                monitored_user_id=user.id,
                trigger=trigger,
                status="running",
                worker_id=self.worker_id,
            )
            session.add(log_row)
            await session.flush()
            return PollClaim(
                user_id=user.id,
                log_id=log_row.id,
                generation=user.poll_generation,
                trigger=trigger,
                manual_token=user.manual_poll_token,
                username=user.username,
                x_user_id=user.x_user_id,
                since_id=user.last_tweet_id,
                pagination_token=user.pagination_token,
                pagination_since_id=user.pagination_since_id,
                pagination_newest_id=user.pagination_newest_id,
                include_replies=user.include_replies,
                include_retweets=user.include_retweets,
            )

    async def _ensure_identity_available(self, user_id: int, x_user_id: str) -> None:
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(MonitoredUser.id).where(
                    MonitoredUser.x_user_id == x_user_id,
                    MonitoredUser.id != user_id,
                )
            )
        if existing is not None:
            raise IdentityConflictError(
                f"X user id {x_user_id} is already monitored by account id {existing}"
            )

    async def _commit_success(
        self,
        claim: PollClaim,
        *,
        x_user_id: str,
        resolved_user: XUser | None,
        batch: TweetBatch,
        started_perf: float,
        lock_key: str,
        lock_token: str,
        lost_lock: asyncio.Event,
    ) -> CommitResult:
        await self._assert_lock(lock_key, lock_token, lost_lock)
        now = datetime.now(UTC)
        rows_by_id = {
            str(item["id"]): self._tweet_values(claim.user_id, x_user_id, item, now)
            for item in batch.tweets
            if item.get("id")
        }
        async with self.session_factory() as session, session.begin():
            polling_settings = await get_polling_settings(session, self.settings, for_update=True)
            user = await session.get(MonitoredUser, claim.user_id, with_for_update=True)
            if user is None or user.poll_generation != claim.generation:
                await self._finalize_log(
                    session,
                    claim.log_id,
                    status="superseded",
                    started_perf=started_perf,
                    error_message="Polling generation was superseded",
                )
                return CommitResult(status="superseded")
            await self._assert_lock(lock_key, lock_token, lost_lock)

            conflict_id = await session.scalar(
                select(MonitoredUser.id)
                .where(
                    MonitoredUser.x_user_id == x_user_id,
                    MonitoredUser.id != claim.user_id,
                )
                .with_for_update()
            )
            if conflict_id is not None:
                raise IdentityConflictError(
                    f"X user id {x_user_id} is already monitored by account id {conflict_id}"
                )

            inserted, newly_inserted_x_ids = await self._upsert_tweets(
                session, list(rows_by_id.values())
            )
            # The generation job is an idempotent DB outbox row committed atomically
            # with tweet ingestion; a crash cannot leave a persisted tweet half-enqueued.
            await enqueue_jobs_for_x_tweet_ids(session, newly_inserted_x_ids)
            if resolved_user is not None:
                user.x_user_id = resolved_user.id
                user.display_name = resolved_user.name

            user.last_polled_at = now
            user.consecutive_failures = 0
            user.last_error = None
            existing_deadline = user.next_poll_at
            pagination = calculate_pagination_state(claim, batch)
            user.last_tweet_id = pagination.last_tweet_id
            user.pagination_token = pagination.token
            user.pagination_since_id = pagination.since_id
            user.pagination_newest_id = pagination.newest_id
            if pagination.token:
                proposed = now + timedelta(seconds=self.settings.pagination_resume_delay_seconds)
                final_status = pagination.status
            else:
                interval = effective_interval(user.poll_interval_seconds, polling_settings)
                proposed = now + timedelta(seconds=interval)
                final_status = pagination.status

            if user.manual_poll_token != claim.manual_token:
                user.status = "queued"
                user.next_poll_at = now
            else:
                if claim.manual_token is not None:
                    user.manual_poll_token = None
                user.status = final_status
                user.next_poll_at = earlier_deadline(existing_deadline, proposed)

            await self._finalize_log(
                session,
                claim.log_id,
                status="success",
                started_perf=started_perf,
                tweets_fetched=batch.result_count,
                tweets_inserted=inserted,
            )
            return CommitResult(status="success", tweets_inserted=inserted, applied=True)

    async def _handle_failure(
        self,
        claim: PollClaim | None,
        *,
        status: str,
        message: str,
        http_status: int | None,
        proposed_deadline: datetime | None,
        rate_limit_reset_at: datetime | None,
        started_perf: float,
        lock_key: str,
        lock_token: str,
        lost_lock: asyncio.Event,
    ) -> bool:
        if claim is None:
            return False
        try:
            await self._assert_lock(lock_key, lock_token, lost_lock)
        except LockLostError as exc:
            await self._finalize_log_only(
                claim.log_id,
                status="lock_lost",
                started_perf=started_perf,
                error_message=str(exc),
            )
            self._observe_result("lock_lost", claim.trigger, started_perf)
            return False

        async with self.session_factory() as session, session.begin():
            polling_settings = await get_polling_settings(session, self.settings, for_update=True)
            user = await session.get(MonitoredUser, claim.user_id, with_for_update=True)
            if user is None or user.poll_generation != claim.generation:
                await self._finalize_log(
                    session,
                    claim.log_id,
                    status="superseded",
                    started_perf=started_perf,
                    error_message="Polling generation was superseded",
                )
                final_status = "superseded"
            else:
                try:
                    await self._assert_lock(lock_key, lock_token, lost_lock)
                except LockLostError as exc:
                    await self._finalize_log(
                        session,
                        claim.log_id,
                        status="lock_lost",
                        started_perf=started_perf,
                        error_message=str(exc),
                    )
                    final_status = "lock_lost"
                else:
                    now = datetime.now(UTC)
                    existing_deadline = user.next_poll_at
                    if proposed_deadline is None:
                        interval = effective_interval(user.poll_interval_seconds, polling_settings)
                        exponent = min(user.consecutive_failures + 1, 7)
                        base_delay = max(interval, min(3600, 30 * (2**exponent)))
                        jittered = max(15, round(base_delay * random.uniform(0.8, 1.2)))
                        proposed_deadline = now + timedelta(seconds=jittered)
                    user.last_polled_at = now
                    user.last_error = message[:2000]
                    user.consecutive_failures += 1
                    if user.manual_poll_token != claim.manual_token:
                        user.status = "queued"
                        user.next_poll_at = now
                    else:
                        if claim.manual_token is not None:
                            user.manual_poll_token = None
                        user.status = status
                        user.next_poll_at = earlier_deadline(existing_deadline, proposed_deadline)
                    await self._finalize_log(
                        session,
                        claim.log_id,
                        status=status,
                        started_perf=started_perf,
                        http_status=http_status,
                        error_message=message[:4000],
                        rate_limit_reset_at=rate_limit_reset_at,
                    )
                    final_status = status
        self._observe_result(final_status, claim.trigger, started_perf)
        if final_status not in {"superseded", "lock_lost"}:
            logger.warning(
                "Poll failed",
                extra={
                    "monitored_user_id": claim.user_id,
                    "generation": claim.generation,
                    "status": final_status,
                    "http_status": http_status,
                    "error": message,
                },
            )
        return False

    async def _upsert_tweets(
        self, session: AsyncSession, rows: list[dict[str, Any]]
    ) -> tuple[int, list[str]]:
        inserted = 0
        newly_inserted_x_ids: list[str] = []
        for offset in range(0, len(rows), TWEET_INSERT_CHUNK_SIZE):
            chunk = rows[offset : offset + TWEET_INSERT_CHUNK_SIZE]
            tweet_ids = [row["tweet_id"] for row in chunk]
            existing = set(
                await session.scalars(select(Tweet.tweet_id).where(Tweet.tweet_id.in_(tweet_ids)))
            )
            statement = mysql_insert(Tweet).values(chunk)
            statement = statement.on_duplicate_key_update(tweet_id=statement.inserted.tweet_id)
            await session.execute(statement)
            new_ids = list(
                dict.fromkeys(tweet_id for tweet_id in tweet_ids if tweet_id not in existing)
            )
            inserted += len(new_ids)
            newly_inserted_x_ids.extend(new_ids)
        return inserted, newly_inserted_x_ids

    async def _set_global_gate(self, reason: str, until: datetime, http_status: int | None) -> None:
        now = datetime.now(UTC)
        ttl = max(1, math.ceil((as_utc(until) - now).total_seconds()))
        payload = json.dumps(
            {
                "reason": reason,
                "http_status": http_status,
                "until": as_utc(until).isoformat().replace("+00:00", "Z"),
            }
        )
        try:
            await self.redis.set(GLOBAL_X_GATE_KEY, payload, ex=ttl)
        except Exception:
            logger.exception("Failed to set global X API gate", extra={"reason": reason})

    async def _global_gate_active(self) -> bool:
        try:
            return bool(await self.redis.exists(GLOBAL_X_GATE_KEY))
        except Exception:
            return True

    async def _assert_lock(
        self,
        lock_key: str,
        lock_token: str,
        lost_lock: asyncio.Event,
    ) -> None:
        if lost_lock.is_set():
            raise LockLostError("Redis polling lease renewal failed or ownership was lost")
        try:
            renewed = await self.redis.eval(
                RENEW_LOCK_SCRIPT,
                1,
                lock_key,
                lock_token,
                self.settings.worker_lock_ttl_seconds,
            )
        except Exception as exc:
            lost_lock.set()
            raise LockLostError("Could not verify Redis polling lease") from exc
        if not renewed:
            lost_lock.set()
            raise LockLostError("Redis polling lease is no longer owned by this worker")

    async def _renew_lock(
        self,
        lock_key: str,
        lock_token: str,
        lost_lock: asyncio.Event,
    ) -> None:
        interval = max(5.0, self.settings.worker_lock_ttl_seconds / 3)
        while True:
            await asyncio.sleep(interval)
            try:
                renewed = await self.redis.eval(
                    RENEW_LOCK_SCRIPT,
                    1,
                    lock_key,
                    lock_token,
                    self.settings.worker_lock_ttl_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                lost_lock.set()
                logger.exception("Polling lock renewal failed", extra={"lock_key": lock_key})
                return
            if not renewed:
                lost_lock.set()
                logger.warning("Polling lock ownership was lost", extra={"lock_key": lock_key})
                return

    def _long_backoff(self) -> datetime:
        delay = round(6 * 3600 * random.uniform(0.9, 1.1))
        return datetime.now(UTC) + timedelta(seconds=delay)

    @staticmethod
    def _tweet_values(
        monitored_user_id: int,
        x_user_id: str,
        payload: dict[str, Any],
        fetched_at: datetime,
    ) -> dict[str, Any]:
        public_metrics = payload.get("public_metrics") or {}
        return {
            "tweet_id": str(payload["id"]),
            "monitored_user_id": monitored_user_id,
            "author_id": str(payload.get("author_id") or x_user_id),
            "text": str(payload.get("text") or ""),
            "lang": payload.get("lang"),
            "conversation_id": payload.get("conversation_id"),
            "posted_at": parse_x_datetime(payload.get("created_at")),
            "like_count": int(public_metrics.get("like_count", 0)),
            "retweet_count": int(public_metrics.get("retweet_count", 0)),
            "reply_count": int(public_metrics.get("reply_count", 0)),
            "quote_count": int(public_metrics.get("quote_count", 0)),
            "bookmark_count": int(public_metrics.get("bookmark_count", 0)),
            "impression_count": int(public_metrics.get("impression_count", 0)),
            "entities": payload.get("entities"),
            "attachments": payload.get("attachments"),
            "referenced_tweets": payload.get("referenced_tweets"),
            "raw_payload": dict(payload),
            "fetched_at": fetched_at,
        }

    @staticmethod
    async def _finalize_log(
        session: AsyncSession,
        log_id: int,
        *,
        status: str,
        started_perf: float,
        tweets_fetched: int = 0,
        tweets_inserted: int = 0,
        http_status: int | None = None,
        error_message: str | None = None,
        rate_limit_reset_at: datetime | None = None,
    ) -> None:
        log_row = await session.get(PollingLog, log_id)
        if log_row is None:
            return
        log_row.status = status
        log_row.finished_at = datetime.now(UTC)
        log_row.duration_ms = round((time.perf_counter() - started_perf) * 1000)
        log_row.tweets_fetched = tweets_fetched
        log_row.tweets_inserted = tweets_inserted
        log_row.http_status = http_status
        log_row.error_message = error_message
        log_row.rate_limit_reset_at = rate_limit_reset_at

    async def _finalize_log_only(
        self,
        log_id: int,
        *,
        status: str,
        started_perf: float,
        error_message: str,
    ) -> None:
        try:
            async with self.session_factory() as session, session.begin():
                await self._finalize_log(
                    session,
                    log_id,
                    status=status,
                    started_perf=started_perf,
                    error_message=error_message[:4000],
                )
        except Exception:
            logger.exception("Failed to finalize polling log", extra={"polling_log_id": log_id})

    @staticmethod
    def _observe_result(status: str, trigger: str, started_perf: float) -> None:
        POLL_RUNS.labels(status=status, trigger=trigger).inc()
        POLL_DURATION.labels(status=status).observe(time.perf_counter() - started_perf)
