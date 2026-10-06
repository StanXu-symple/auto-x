"""Durable automatic publication of generated listening-task drafts."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.models.admin import Admin
from app.models.ai import AIDraft, AIGenerationJob, ArticlePublishAttempt
from app.models.ai_publish import AIPublishDispatch
from app.models.qq import QQBotAccount, QQDelivery, QQJoinedGroup
from app.models.tweet import Tweet
from app.models.tweet_screenshot import TweetScreenshot
from app.services.article_media import (
    SOURCE_SCREENSHOT_IMAGES_KEY,
    article_image_path,
    article_screenshot_copies,
    snapshot_article_screenshot,
)
from app.services.qq_notifications import enqueue_qq_delivery_ids, split_qq_text
from app.services.tweet_screenshot_media import screenshot_path
from app.services.xhs_client import XHSServiceClient, validated_source
from app.services.xhs_credentials import has_xhs_credentials
from app.services.xhs_jobs import XHSJobNotAcceptedError, submit_xhs_job
from app.services.xhs_limits import XHS_NOTE_CONTENT_MAX_LENGTH, XHS_NOTE_TITLE_MAX_LENGTH

logger = logging.getLogger(__name__)
CHANNELS = ("xhs", "qq")
PREFLIGHT_RETRY_LIMIT = 12
XHS_NOT_ACCEPTED_RETRY_LIMIT = 5
XHS_SAME_ADMIN_WAIT_SECONDS = 10


class AwaitingSourceScreenshot(RuntimeError):
    """The requested source image is still in the screenshot queue."""


class AIPublishXHSClient(XHSServiceClient):
    client_id = "ai-worker"


def _channels(snapshot: dict | None) -> list[str]:
    values = (snapshot or {}).get("auto_publish_channels") or []
    if not isinstance(values, list):
        return []
    return [channel for channel in CHANNELS if channel in values]


def _payload_snapshot(job: AIGenerationJob, draft: AIDraft) -> dict:
    task = job.task_snapshot if isinstance(job.task_snapshot, dict) else {}
    metadata = draft.draft_metadata or {}
    return {
        "title": draft.title,
        "content": draft.content,
        "excerpt": draft.excerpt,
        "images": list(draft.images or []),
        "include_source_screenshot": metadata.get("include_source_screenshot") is not False,
        "source_tweet_id": draft.source_tweet_id,
        "draft_revision": draft.revision,
        "owner_admin_id": task.get("owner_admin_id"),
        "qq_bot_id": task.get("qq_bot_id"),
        "qq_group_openids": task.get("qq_group_openids") or [],
        "qq_target": task.get("qq_target"),
    }


async def initialize_publish_dispatches(
    session: AsyncSession, job: AIGenerationJob, draft: AIDraft
) -> int:
    """Called in the draft transaction; the job lock is already held."""
    if job.publish_outbox_initialized_at is not None:
        return 0
    channels = _channels(job.task_snapshot)
    payload = _payload_snapshot(job, draft)
    for channel in channels:
        session.add(
            AIPublishDispatch(
                job_id=job.id,
                draft_id=draft.id,
                owner_admin_id=(
                    payload["owner_admin_id"]
                    if isinstance(payload["owner_admin_id"], int)
                    and payload["owner_admin_id"] > 0
                    else None
                ),
                channel=channel,
                status="pending",
                attempts=0,
                next_attempt_at=datetime.now(UTC),
                payload_snapshot=payload,
            )
        )
    job.publish_outbox_initialized_at = datetime.now(UTC)
    await session.flush()
    return len(channels)


async def retry_failed_dispatch(
    session: AsyncSession, dispatch_id: int
) -> AIPublishDispatch | None:
    """Reset a preflight failure only; sent or ambiguous requests cannot be replayed."""
    row = await session.get(AIPublishDispatch, dispatch_id, with_for_update=True)
    if row is None or row.status != "failed" or row.article_publish_attempt_id is not None:
        return None
    row.status = "pending"
    row.next_attempt_at = datetime.now(UTC)
    row.last_error = None
    row.completed_at = None
    row.rejection_attempts = 0
    return row


class AIPublishDispatcher:
    def __init__(
        self,
        settings: Settings,
        redis: Redis,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.redis = redis
        self.session_factory = session_factory
        self.stop_event = asyncio.Event()
        self.active: set[asyncio.Task[None]] = set()
        self.xhs_client: AIPublishXHSClient | None = None
        self.xhs_client_lock = asyncio.Lock()

    async def _ensure_xhs_client(self) -> AIPublishXHSClient | None:
        if self.settings.xhs_transport != "http":
            return None
        async with self.xhs_client_lock:
            if self.xhs_client is None:
                self.xhs_client = AIPublishXHSClient(self.settings)
            return self.xhs_client

    async def run(self) -> None:
        try:
            while not self.stop_event.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("AI auto publication scan failed")
                try:
                    await asyncio.wait_for(
                        self.stop_event.wait(),
                        timeout=self.settings.ai_worker_scan_interval_seconds,
                    )
                except TimeoutError:
                    pass
        finally:
            for task in self.active:
                task.cancel()
            if self.active:
                await asyncio.gather(*self.active, return_exceptions=True)
            if self.xhs_client is not None:
                await self.xhs_client.aclose()

    def request_stop(self) -> None:
        self.stop_event.set()

    async def run_once(self) -> None:
        await self._seed_uninitialized()
        await self._mark_abandoned_xhs()
        await self._reconcile_qq()
        self.active = {task for task in self.active if not task.done()}
        capacity = max(0, min(4, self.settings.ai_worker_max_concurrency) - len(self.active))
        if not capacity:
            return
        async with self.session_factory() as session:
            ids = list(
                await session.scalars(
                    select(AIPublishDispatch.id)
                    .where(
                        AIPublishDispatch.status.in_(("pending", "retry_wait")),
                        AIPublishDispatch.next_attempt_at <= datetime.now(UTC),
                    )
                    .order_by(AIPublishDispatch.next_attempt_at, AIPublishDispatch.id)
                    .limit(capacity)
                )
            )
        for dispatch_id in ids:
            task = asyncio.create_task(self._process_guarded(dispatch_id))
            self.active.add(task)

    async def _process_guarded(self, dispatch_id: int) -> None:
        try:
            await self.process_dispatch(dispatch_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "AI auto publication dispatch failed", extra={"dispatch_id": dispatch_id}
            )

    async def _seed_uninitialized(self) -> None:
        # Recovers a generated draft if a savepoint could not create its outbox.
        async with self.session_factory() as session, session.begin():
            jobs = list(
                await session.scalars(
                    select(AIGenerationJob)
                    .where(
                        AIGenerationJob.status == "succeeded",
                        AIGenerationJob.listen_task_id.is_not(None),
                        AIGenerationJob.publish_outbox_initialized_at.is_(None),
                    )
                    .order_by(AIGenerationJob.id)
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                draft = await session.scalar(select(AIDraft).where(AIDraft.job_id == job.id))
                if draft is None:
                    # A deleted draft cannot be dispatched; do not let one
                    # damaged historic job starve the recovery scan forever.
                    logger.warning("Succeeded AI job has no draft", extra={"ai_job_id": job.id})
                    job.publish_outbox_initialized_at = datetime.now(UTC)
                    continue
                await initialize_publish_dispatches(session, job, draft)

    async def _mark_abandoned_xhs(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(
            seconds=self.settings.xhs_job_timeout_seconds + 60
        )
        async with self.session_factory() as session, session.begin():
            rows = list(
                await session.scalars(
                    select(AIPublishDispatch)
                    .where(
                        AIPublishDispatch.channel == "xhs",
                        AIPublishDispatch.status == "dispatching",
                        AIPublishDispatch.started_at <= cutoff,
                    )
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                await self._finish(
                    session,
                    row,
                    "uncertain",
                    "小红书请求中断，发布结果未知；请先核对账号笔记，勿自动重发",
                )

    async def _reconcile_qq(self) -> None:
        async with self.session_factory() as session, session.begin():
            active_delivery = select(QQDelivery.id).where(
                QQDelivery.article_publish_attempt_id
                == AIPublishDispatch.article_publish_attempt_id,
                QQDelivery.status.in_(("queued", "retry_wait", "sending")),
            )
            rows = list(
                await session.scalars(
                    select(AIPublishDispatch)
                    .where(
                        AIPublishDispatch.channel == "qq",
                        AIPublishDispatch.status == "accepted",
                        ~active_delivery.exists(),
                    )
                    .order_by(AIPublishDispatch.id)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                deliveries = list(
                    await session.scalars(
                        select(QQDelivery).where(
                            QQDelivery.article_publish_attempt_id
                            == row.article_publish_attempt_id
                        )
                    )
                )
                # A failed message does not stop the other groups or messages.
                # Keep the draft protected until every remaining send is over.
                if any(
                    delivery.status in {"queued", "retry_wait", "sending"}
                    for delivery in deliveries
                ):
                    continue
                attempt = await session.get(ArticlePublishAttempt, row.article_publish_attempt_id)
                if attempt is None:
                    await self._finish(session, row, "failed", "QQ 发布尝试记录丢失")
                    continue
                if len(deliveries) != attempt.delivery_count or not deliveries:
                    error = (
                        f"QQ 投递记录不完整：应有 {attempt.delivery_count} 条，"
                        f"现有 {len(deliveries)} 条；请核对群消息"
                    )
                    attempt.status = "failed"
                    attempt.error = error
                    attempt.completed_at = datetime.now(UTC)
                    await self._finish(session, row, "failed", error)
                    continue
                failed = next(
                    (delivery for delivery in deliveries if delivery.status != "sent"), None
                )
                if failed is not None:
                    error = failed.last_error or f"QQ 投递未成功（{failed.status}）"
                    attempt.status = "failed"
                    attempt.error = error[:2000]
                    attempt.completed_at = datetime.now(UTC)
                    await self._finish(session, row, "failed", error)
                else:
                    attempt.status = "published"
                    attempt.error = None
                    attempt.completed_at = datetime.now(UTC)
                    await self._finish(session, row, "published")

    async def process_dispatch(self, dispatch_id: int) -> None:
        async with self.session_factory() as session:
            row = await session.get(AIPublishDispatch, dispatch_id)
            if row is None or row.status not in {"pending", "retry_wait"}:
                return
            channel = row.channel
        if channel == "xhs":
            await self._dispatch_xhs(dispatch_id)
        elif channel == "qq":
            await self._dispatch_qq(dispatch_id)

    async def _dispatch_xhs(self, dispatch_id: int) -> None:
        try:
            payload, paths, admin_id = await self._prepare_media(dispatch_id, require_image=True)
        except AwaitingSourceScreenshot as exc:
            await self._preflight_failure(dispatch_id, str(exc), retry=True)
            return
        except (ValueError, OSError) as exc:
            await self._preflight_failure(dispatch_id, str(exc), retry=False)
            return
        try:
            xhs_client = await self._ensure_xhs_client()
        except (OSError, ValueError) as exc:
            await self._preflight_failure(
                dispatch_id, f"小红书服务身份未配置：{type(exc).__name__}", retry=False
            )
            return
        attempt_id = str(uuid.uuid4())
        async with self.session_factory() as session, session.begin():
            # All AI workers use the same database row as the per-account
            # reservation mutex. It is held only until dispatching is committed.
            admin = await session.get(Admin, admin_id, with_for_update=True)
            row = await session.get(AIPublishDispatch, dispatch_id, with_for_update=True)
            if row is None or row.status not in {"pending", "retry_wait"}:
                return
            if admin is None:
                await self._finish(session, row, "failed", "归属管理员已不存在")
                return
            active_id = await session.scalar(
                select(AIPublishDispatch.id)
                .where(
                    AIPublishDispatch.channel == "xhs",
                    AIPublishDispatch.owner_admin_id == admin_id,
                    AIPublishDispatch.status == "dispatching",
                    AIPublishDispatch.id != dispatch_id,
                )
                .limit(1)
            )
            if active_id is not None:
                row.status = "retry_wait"
                row.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=XHS_SAME_ADMIN_WAIT_SECONDS
                )
                row.last_error = "同一小红书账号的上一条自动推送仍在执行"
                return
            row.status = "dispatching"
            row.attempts += 1
            row.started_at = datetime.now(UTC)
            row.article_publish_attempt_id = attempt_id
            row.last_error = None
            session.add(
                ArticlePublishAttempt(
                    attempt_id=attempt_id,
                    article_id=row.draft_id,
                    channel="xhs",
                    status="queued",
                    target_summary=f"管理员 {admin_id} 的小红书账号",
                    delivery_count=1,
                    started_at=row.started_at,
                )
            )
        try:
            post = {"title": payload["title"], "content": payload["content"], "images": paths}
            if xhs_client is not None:
                await xhs_client.submit(
                    operation="post",
                    admin_id=admin_id,
                    payload=post,
                    timeout_seconds=self.settings.xhs_job_timeout_seconds + 5,
                )
            else:
                await submit_xhs_job(
                    self.redis,
                    operation="post",
                    admin_id=admin_id,
                    payload=post,
                    timeout_seconds=self.settings.xhs_job_timeout_seconds + 5,
                )
        except XHSJobNotAcceptedError as exc:
            await self._not_accepted(dispatch_id, exc)
            return
        except asyncio.CancelledError:
            # The request may have reached XHS; the abandoned scan records uncertainty.
            raise
        except Exception as exc:
            logger.warning(
                "AI draft XHS result uncertain",
                extra={"dispatch_id": dispatch_id, "error_type": type(exc).__name__},
            )
            async with self.session_factory() as session, session.begin():
                row = await session.get(AIPublishDispatch, dispatch_id, with_for_update=True)
                if row is not None and row.status == "dispatching":
                    await self._finish(
                        session, row, "uncertain",
                        "小红书请求未确认成功；请核对账号笔记后再决定是否重新发布",
                    )
            return
        async with self.session_factory() as session, session.begin():
            row = await session.get(AIPublishDispatch, dispatch_id, with_for_update=True)
            if row is not None and row.status == "dispatching":
                await self._finish(session, row, "published")

    async def _not_accepted(
        self, dispatch_id: int, error: XHSJobNotAcceptedError
    ) -> None:
        """The service rejected before storing the job; replay is safe and bounded."""
        async with self.session_factory() as session, session.begin():
            row = await session.get(AIPublishDispatch, dispatch_id, with_for_update=True)
            if row is None or row.status != "dispatching":
                return
            now = datetime.now(UTC)
            attempt = await session.get(ArticlePublishAttempt, row.article_publish_attempt_id)
            if attempt is not None:
                attempt.status = "failed"
                attempt.error = str(error)[:2000]
                attempt.completed_at = now
            row.rejection_attempts += 1
            row.article_publish_attempt_id = None
            row.started_at = None
            row.last_error = str(error)[:2000]
            if row.rejection_attempts >= XHS_NOT_ACCEPTED_RETRY_LIMIT:
                row.status = "failed"
                row.completed_at = now
                return
            delay = min(120, 5 * (2 ** (row.rejection_attempts - 1)))
            if error.retry_after_seconds is not None:
                delay = max(delay, min(120, error.retry_after_seconds))
            row.status = "retry_wait"
            row.next_attempt_at = now + timedelta(seconds=delay)
            row.completed_at = None

    async def _dispatch_qq(self, dispatch_id: int) -> None:
        try:
            payload, paths, _ = await self._prepare_media(dispatch_id, require_image=False)
        except (ValueError, OSError) as exc:
            await self._preflight_failure(dispatch_id, str(exc), retry=False)
            return
        target = payload.get("qq_target") if isinstance(payload.get("qq_target"), dict) else {}
        bot_id = payload.get("qq_bot_id") or target.get("bot_id")
        groups = payload.get("qq_group_openids") or target.get("group_openids") or []
        if not isinstance(bot_id, int) or bot_id < 1 or not isinstance(groups, list) or not groups:
            await self._preflight_failure(
                dispatch_id, "监听任务未配置 QQ 机器人和目标群", retry=False
            )
            return
        groups = sorted(
            set(group.strip() for group in groups if isinstance(group, str) and group.strip())
        )
        if not groups:
            await self._preflight_failure(dispatch_id, "监听任务的 QQ 目标群为空", retry=False)
            return
        async with self.session_factory() as session, session.begin():
            row = await session.get(AIPublishDispatch, dispatch_id, with_for_update=True)
            if row is None or row.status not in {"pending", "retry_wait"}:
                return
            bot = await session.get(QQBotAccount, bot_id)
            if bot is None or not bot.is_enabled:
                await self._finish(session, row, "failed", "所选 QQ 机器人不存在或未启用")
                return
            joined = set(
                await session.scalars(
                    select(QQJoinedGroup.group_openid).where(
                        QQJoinedGroup.bot_id == bot.id,
                        QQJoinedGroup.app_id == bot.app_id,
                        QQJoinedGroup.is_joined.is_(True),
                        QQJoinedGroup.group_openid.in_(groups),
                    )
                )
            )
            if set(groups) != joined:
                await self._finish(session, row, "failed", "机器人尚未加入所选 QQ 群")
                return
            now = datetime.now(UTC)
            attempt_id = str(uuid.uuid4())
            text = "\n".join(
                (
                    f"标题:{payload['title']}",
                    f"摘要:{payload.get('excerpt') or ''}",
                    f"正文:{payload['content']}",
                )
            )
            deliveries: list[QQDelivery] = []
            for group in groups:
                for sequence, (body, media_path) in enumerate(
                    [(body, None) for body in split_qq_text(text)]
                    + [("", path) for path in paths]
                ):
                    deliveries.append(
                        QQDelivery(
                            article_id=row.draft_id,
                            article_publish_attempt_id=attempt_id,
                            sequence=sequence,
                            media_path=media_path,
                            target_id=None,
                            source_tweet_id=None,
                            kind="article",
                            idempotency_key=f"ai:{row.job_id}:qq:{group}:{sequence}",
                            bot_name=bot.name,
                            bot_app_id=bot.app_id,
                            bot_version=bot.version,
                            target_name=group,
                            group_openid=group,
                            message_body=body,
                            status="queued",
                            attempts=0,
                            max_attempts=self.settings.qq_worker_max_attempts,
                            next_attempt_at=now,
                        )
                    )
            row.status = "accepted"
            row.attempts += 1
            row.started_at = now
            row.article_publish_attempt_id = attempt_id
            row.last_error = None
            session.add(
                ArticlePublishAttempt(
                    attempt_id=attempt_id,
                    article_id=row.draft_id,
                    channel="qq",
                    status="queued",
                    target_summary=f"{bot.name} · {len(groups)} 个群",
                    delivery_count=len(deliveries),
                    started_at=now,
                )
            )
            session.add_all(deliveries)
            await session.flush()
            delivery_ids = [delivery.id for delivery in deliveries]
        await enqueue_qq_delivery_ids(self.redis, delivery_ids)

    async def _prepare_media(
        self, dispatch_id: int, *, require_image: bool
    ) -> tuple[dict, list[str], int]:
        async with self.session_factory() as session, session.begin():
            row = await session.get(AIPublishDispatch, dispatch_id)
            if row is None:
                raise ValueError("自动投递记录不存在")
            payload = dict(row.payload_snapshot or {})
            admin_id = payload.get("owner_admin_id")
            if require_image and (not isinstance(admin_id, int) or admin_id < 1):
                raise ValueError("监听任务缺少小红书账号归属管理员")
            if not isinstance(admin_id, int) or admin_id < 1:
                admin_id = 0
            if require_image:
                owner = await session.get(Admin, admin_id)
                if owner is None or not owner.is_active:
                    raise ValueError("任务归属管理员已停用，不能自动推送小红书")
                if not await has_xhs_credentials(session, admin_id=admin_id):
                    raise ValueError("归属管理员尚未保存小红书登录态")
                title = payload.get("title")
                content = payload.get("content")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("小红书标题为空")
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("小红书正文为空")
                if len(title) > XHS_NOTE_TITLE_MAX_LENGTH:
                    raise ValueError(f"小红书标题不能超过 {XHS_NOTE_TITLE_MAX_LENGTH} 个字符")
                if len(content) > XHS_NOTE_CONTENT_MAX_LENGTH:
                    raise ValueError(f"小红书正文不能超过 {XHS_NOTE_CONTENT_MAX_LENGTH} 个字符")
            images = payload.get("images") or []
            if not isinstance(images, list):
                raise ValueError("文章图片配置无效")
            paths: list[str] = []
            for image in images:
                path = await asyncio.to_thread(
                    article_image_path, image, admin_id=admin_id or None
                )
                if path is None:
                    raise ValueError("文章包含不存在的图片")
                paths.append(str(path))
            if payload.get("include_source_screenshot") and payload.get("source_tweet_id"):
                tweet = await session.get(Tweet, payload["source_tweet_id"])
                screenshot = await session.get(TweetScreenshot, tweet.tweet_id) if tweet else None
                if screenshot is not None and screenshot.status == "succeeded":
                    source = await asyncio.to_thread(screenshot_path, screenshot.image_path)
                    if source is None:
                        raise ValueError("原帖截图文件不存在")
                    if admin_id:
                        image, path = await asyncio.to_thread(
                            snapshot_article_screenshot,
                            source,
                            article_id=row.draft_id,
                            admin_id=admin_id,
                        )
                        draft = await session.get(AIDraft, row.draft_id, with_for_update=True)
                        if draft is not None:
                            copies = article_screenshot_copies(draft)
                            draft.draft_metadata = {
                                **(draft.draft_metadata or {}),
                                SOURCE_SCREENSHOT_IMAGES_KEY: list(dict.fromkeys([*copies, image])),
                            }
                        paths.insert(0, str(path))
                elif require_image and not paths and (
                    screenshot is None or screenshot.status in {"pending", "running", "retry_wait"}
                ):
                    raise AwaitingSourceScreenshot("原帖截图尚未完成，稍后重试")
            if require_image and not paths:
                raise ValueError("小红书图文推送至少需要一张图片，原帖截图尚不可用")
            if require_image and len(paths) > 18:
                raise ValueError("含原帖截图在内，小红书最多发布 18 张图片")
            if require_image:
                for path in paths:
                    await asyncio.to_thread(validated_source, path, admin_id)
            return payload, paths, admin_id

    async def _preflight_failure(self, dispatch_id: int, message: str, *, retry: bool) -> None:
        async with self.session_factory() as session, session.begin():
            row = await session.get(AIPublishDispatch, dispatch_id, with_for_update=True)
            if row is None or row.status not in {"pending", "retry_wait"}:
                return
            row.attempts += 1
            if retry and row.attempts < PREFLIGHT_RETRY_LIMIT:
                row.status = "retry_wait"
                row.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=min(300, 10 * (2 ** min(5, row.attempts - 1)))
                )
                row.last_error = message[:2000]
            else:
                await self._finish(session, row, "failed", message)

    @staticmethod
    async def _finish(
        session: AsyncSession, row: AIPublishDispatch, status: str, error: str | None = None
    ) -> None:
        now = datetime.now(UTC)
        row.status = status
        row.last_error = error[:2000] if error else None
        row.completed_at = now
        if row.article_publish_attempt_id:
            attempt = await session.get(ArticlePublishAttempt, row.article_publish_attempt_id)
            if attempt is not None and attempt.status == "queued":
                attempt.status = "published" if status == "published" else "failed"
                attempt.error = row.last_error
                attempt.completed_at = now
