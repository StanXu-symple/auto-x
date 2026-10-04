import asyncio
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.core.config import get_settings
from app.models.ai import AIDraft, ArticlePublishAttempt
from app.models.monitored_user import MonitoredUser
from app.models.qq import QQBotAccount, QQDelivery, QQJoinedGroup
from app.models.tweet import Tweet
from app.models.tweet_screenshot import TweetScreenshot
from app.schemas.article import (
    ArticleCreate,
    ArticleOut,
    ArticlePatch,
    ArticlePublishAccepted,
    ArticlePublishCreate,
    ArticlePublishHistoryOut,
    ArticlePublishStatus,
    ArticleSource,
    ArticleSourceScreenshot,
)
from app.schemas.common import MessageResponse, Page
from app.services.article_media import (
    ALLOWED_IMAGE_SUFFIXES,
    INCLUDE_SOURCE_SCREENSHOT_KEY,
    MAX_ARTICLE_IMAGE_BYTES,
    SOURCE_SCREENSHOT_IMAGES_KEY,
    article_image_path,
    article_includes_source_screenshot,
    article_screenshot_copies,
    clear_article_images,
    clear_unreferenced_article_images,
    snapshot_article_screenshot,
    write_article_image,
)
from app.services.qq_notifications import enqueue_qq_delivery_ids, split_qq_text
from app.services.tweet_screenshot_media import screenshot_path
from app.services.xhs_credentials import has_xhs_credentials
from app.services.xhs_jobs import (
    XHSJobTimeoutError,
    XHSWorkerUnavailableError,
    submit_xhs_job,
)
from app.services.xhs_limits import (
    XHS_NOTE_CONTENT_MAX_LENGTH,
    XHS_NOTE_TITLE_MAX_LENGTH,
)
from app.services.xhs_verification import clear_verification_image

router = APIRouter(prefix="/articles", tags=["Article Management"])
logger = logging.getLogger(__name__)


def _article_out(
    article: AIDraft,
    source_url: str | None = None,
    source_screenshot: ArticleSourceScreenshot | None = None,
) -> ArticleOut:
    include_source_screenshot = article_includes_source_screenshot(article)
    return ArticleOut(
        id=article.id,
        job_id=article.job_id,
        source_tweet_id=article.source_tweet_id,
        source_url=source_url,
        source_screenshot=source_screenshot if include_source_screenshot else None,
        include_source_screenshot=include_source_screenshot,
        article_source=article.article_source,
        title=article.title,
        content=article.content,
        excerpt=article.excerpt,
        images=article.images or [],
        publish_status=article.publish_status or "unpublished",
        publish_channel=article.publish_channel,
        publish_error=article.publish_error,
        published_at=article.published_at,
        revision=article.revision,
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


def _source_url(tweet: Tweet, username: str) -> str | None:
    if re.fullmatch(r"[0-9]{1,32}", tweet.tweet_id) is None:
        return None
    payload = tweet.raw_payload if isinstance(tweet.raw_payload, dict) else {}
    stored_url = payload.get("source_url")
    if isinstance(stored_url, str):
        try:
            parsed = urlsplit(stored_url)
            port = parsed.port
        except ValueError:
            parsed = None
        if parsed is not None:
            match = re.fullmatch(r"/([A-Za-z0-9_]{1,15})/status/([0-9]{1,32})/?", parsed.path)
            if (
                parsed.scheme == "https"
                and parsed.hostname in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
                and parsed.username is None
                and parsed.password is None
                and port in (None, 443)
                and match
                and match.group(2) == tweet.tweet_id
            ):
                return f"https://x.com/{match.group(1)}/status/{tweet.tweet_id}"
    if re.fullmatch(r"[A-Za-z0-9_]{1,15}", username) is None:
        return None
    return f"https://x.com/{username}/status/{tweet.tweet_id}"


async def _article_sources(
    db: DbSession, articles: list[AIDraft]
) -> dict[int, tuple[str | None, ArticleSourceScreenshot | None]]:
    tweet_ids = {
        article.source_tweet_id for article in articles if article.source_tweet_id is not None
    }
    if not tweet_ids:
        return {}
    sources = await db.execute(
        select(Tweet, MonitoredUser.username, TweetScreenshot)
        .join(MonitoredUser, Tweet.monitored_user_id == MonitoredUser.id)
        .outerjoin(TweetScreenshot, TweetScreenshot.tweet_id == Tweet.tweet_id)
        .where(Tweet.id.in_(tweet_ids))
    )
    result = {}
    for tweet, username, screenshot in sources:
        source_screenshot = None
        if screenshot is not None and screenshot.status == "succeeded":
            path = await asyncio.to_thread(screenshot_path, screenshot.image_path)
            if path is not None:
                source_screenshot = ArticleSourceScreenshot(
                    tweet_id=tweet.tweet_id,
                    captured_at=screenshot.captured_at,
                    sha256=screenshot.sha256,
                )
        result[tweet.id] = (_source_url(tweet, username), source_screenshot)
    return result


async def _source_screenshot_path(db: DbSession, article: AIDraft) -> Path | None:
    if article.source_tweet_id is None or not article_includes_source_screenshot(article):
        return None
    screenshot = await db.scalar(
        select(TweetScreenshot)
        .join(Tweet, Tweet.tweet_id == TweetScreenshot.tweet_id)
        .where(Tweet.id == article.source_tweet_id, TweetScreenshot.status == "succeeded")
    )
    if screenshot is None:
        return None
    return await asyncio.to_thread(screenshot_path, screenshot.image_path)


async def _snapshot_source_screenshot(
    article: AIDraft, source: Path | None, admin: CurrentAdmin
) -> Path | None:
    if source is None:
        return None
    image, path = await asyncio.to_thread(
        snapshot_article_screenshot, source, article_id=article.id, admin_id=admin.id
    )
    article.draft_metadata = {
        **(article.draft_metadata or {}),
        SOURCE_SCREENSHOT_IMAGES_KEY: list(
            dict.fromkeys([*article_screenshot_copies(article), image])
        ),
    }
    return path


def _publish_history_out(attempt: ArticlePublishAttempt) -> ArticlePublishHistoryOut:
    return ArticlePublishHistoryOut.model_validate(attempt)


@router.get("", response_model=Page[ArticleOut])
@router.get("/", response_model=Page[ArticleOut], include_in_schema=False)
async def list_articles(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=200),
    article_source: ArticleSource | None = None,
    publish_status: ArticlePublishStatus | None = None,
) -> Page[ArticleOut]:
    conditions = []
    normalized_keyword = keyword.strip() if keyword else ""
    if normalized_keyword:
        conditions.append(
            or_(
                AIDraft.title.contains(normalized_keyword, autoescape=True),
                AIDraft.content.contains(normalized_keyword, autoescape=True),
                AIDraft.excerpt.contains(normalized_keyword, autoescape=True),
            )
        )
    if article_source:
        conditions.append(AIDraft.article_source == article_source)
    if publish_status:
        conditions.append(AIDraft.publish_status == publish_status)

    total = int(await db.scalar(select(func.count(AIDraft.id)).where(*conditions)) or 0)
    articles = list(
        await db.scalars(
            select(AIDraft)
            .where(*conditions)
            .order_by(AIDraft.updated_at.desc(), AIDraft.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    sources = await _article_sources(db, articles)
    return Page(
        items=[
            _article_out(article, *sources.get(article.source_tweet_id, (None, None)))
            for article in articles
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
async def create_article(payload: ArticleCreate, db: DbSession, admin: CurrentAdmin) -> ArticleOut:
    for image in payload.images:
        if await asyncio.to_thread(article_image_path, image, admin_id=admin.id) is None:
            raise APIError(400, "article_image_invalid", "图片不存在或不属于当前用户")
    article = AIDraft(
        job_id=None,
        source_tweet_id=None,
        article_source="user",
        title=payload.title,
        content=payload.content,
        excerpt=payload.excerpt,
        images=payload.images,
        publish_status="unpublished",
        draft_metadata=None,
        revision=1,
    )
    db.add(article)
    await db.commit()
    await db.refresh(article)
    sources = await _article_sources(db, [article])
    return _article_out(article, *sources.get(article.source_tweet_id, (None, None)))


@router.patch("/{article_id}", response_model=ArticleOut)
async def update_article(
    article_id: int,
    payload: ArticlePatch,
    db: DbSession,
    admin: CurrentAdmin,
) -> ArticleOut:
    article = await db.scalar(select(AIDraft).where(AIDraft.id == article_id).with_for_update())
    if article is None:
        raise APIError(404, "article_not_found", "文章不存在")
    if article.publish_status == "queued":
        raise APIError(409, "article_publish_in_progress", "文章正在推送，暂时不能编辑")
    if article.revision != payload.revision:
        raise APIError(
            409,
            "article_revision_conflict",
            "文章已被其他请求修改，请刷新后重试",
            {"current_revision": article.revision},
        )
    changes = payload.model_dump(exclude_unset=True, exclude={"revision"})
    for image in changes.get("images") or []:
        if await asyncio.to_thread(article_image_path, image, admin_id=admin.id) is None:
            raise APIError(400, "article_image_invalid", "图片不存在或不属于当前用户")
    old_images = list(article.images or [])
    for key, value in changes.items():
        if key == INCLUDE_SOURCE_SCREENSHOT_KEY:
            article.draft_metadata = {**(article.draft_metadata or {}), key: value}
        else:
            setattr(article, key, value)
    if {"title", "content", "excerpt", "images", INCLUDE_SOURCE_SCREENSHOT_KEY} & changes.keys():
        article.publish_status = "unpublished"
        article.publish_channel = None
        article.publish_attempt_id = None
        article.publish_error = None
        article.published_at = None
    article.revision += 1
    await db.commit()
    await db.refresh(article)
    if "images" in changes:
        await clear_unreferenced_article_images(
            db, [image for image in old_images if image not in (article.images or [])]
        )
    sources = await _article_sources(db, [article])
    return _article_out(article, *sources.get(article.source_tweet_id, (None, None)))


@router.delete("/{article_id}", response_model=MessageResponse)
async def delete_article(article_id: int, db: DbSession, _: CurrentAdmin) -> MessageResponse:
    article = await db.get(AIDraft, article_id)
    if article is None:
        raise APIError(404, "article_not_found", "文章不存在")
    if article.publish_status == "queued":
        raise APIError(409, "article_publish_in_progress", "文章正在推送，暂时不能删除")
    images = [*(article.images or []), *article_screenshot_copies(article)]
    await db.delete(article)
    await db.commit()
    await clear_unreferenced_article_images(db, images)
    return MessageResponse(message="文章已删除")


@router.post("/uploads")
async def upload_article_images(admin: CurrentAdmin, files: list[UploadFile] = File(...)) -> dict:
    if len(files) > 18:
        raise APIError(422, "too_many_article_images", "每次最多上传 18 张图片")
    for file in files:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise APIError(400, "unsupported_article_image", "仅支持 JPG、PNG、WebP 图片")
    result = []
    try:
        for file in files:
            suffix = Path(file.filename or "").suffix.lower()
            contents = await file.read(MAX_ARTICLE_IMAGE_BYTES + 1)
            if not contents:
                raise APIError(400, "empty_article_image", "不能上传空图片")
            if len(contents) > MAX_ARTICLE_IMAGE_BYTES:
                raise APIError(413, "article_image_too_large", "单张图片不能超过 10 MB")
            relative_name = f"{admin.id}/{os.urandom(12).hex()}{suffix}"
            await asyncio.to_thread(write_article_image, relative_name, contents)
            result.append({"path": relative_name})
    except Exception:
        await asyncio.to_thread(clear_article_images, [str(item["path"]) for item in result])
        raise
    return {"files": result}


@router.get("/images/{owner_id}/{filename}")
async def get_article_image(owner_id: int, filename: str, admin: CurrentAdmin) -> FileResponse:
    image = f"{owner_id}/{filename}"
    path = await asyncio.to_thread(article_image_path, image, admin_id=admin.id)
    if path is None:
        raise APIError(404, "article_image_not_found", "图片不存在")
    return FileResponse(path)


@router.get("/{article_id}/publish-history", response_model=Page[ArticlePublishHistoryOut])
async def article_publish_history(
    article_id: int,
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> Page[ArticlePublishHistoryOut]:
    if await db.get(AIDraft, article_id) is None:
        raise APIError(404, "article_not_found", "文章不存在")
    condition = ArticlePublishAttempt.article_id == article_id
    total = int(
        await db.scalar(select(func.count(ArticlePublishAttempt.attempt_id)).where(condition))
        or 0
    )
    attempts = list(
        await db.scalars(
            select(ArticlePublishAttempt)
            .where(condition)
            .order_by(
                ArticlePublishAttempt.created_at.desc(),
                ArticlePublishAttempt.attempt_id.desc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(
        items=[_publish_history_out(attempt) for attempt in attempts],
        total=total,
        page=page,
        page_size=page_size,
    )


def _article_qq_text(article: AIDraft) -> str:
    return "\n".join(
        (f"标题:{article.title}", f"摘要:{article.excerpt or ''}", f"正文:{article.content}")
    )


async def _publish_to_qq(
    article: AIDraft,
    payload: ArticlePublishCreate,
    db: DbSession,
    redis: RedisClient,
    admin: CurrentAdmin,
) -> ArticlePublishAccepted:
    assert payload.bot_id is not None
    bot = await db.get(QQBotAccount, payload.bot_id)
    if bot is None or not bot.is_enabled:
        raise APIError(409, "qq_bot_disabled", "所选 QQ 机器人不存在或未启用")
    groups = set(payload.group_openids)
    joined = set(
        await db.scalars(
            select(QQJoinedGroup.group_openid).where(
                QQJoinedGroup.bot_id == bot.id,
                QQJoinedGroup.app_id == bot.app_id,
                QQJoinedGroup.is_joined.is_(True),
                QQJoinedGroup.group_openid.in_(groups),
            )
        )
    )
    if missing := groups - joined:
        raise APIError(422, "qq_group_not_joined", "机器人尚未加入所选群", sorted(missing))
    image_paths: list[Path] = []
    for image in article.images or []:
        path = await asyncio.to_thread(article_image_path, image)
        if path is None:
            raise APIError(400, "article_image_invalid", "文章包含不存在的图片")
        image_paths.append(path)
    source = await _source_screenshot_path(db, article)
    source_copy = await _snapshot_source_screenshot(article, source, admin)
    if source_copy is not None:
        image_paths.insert(0, source_copy)

    attempt_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    deliveries: list[QQDelivery] = []
    for group in sorted(joined):
        sequence = 0
        for body in split_qq_text(_article_qq_text(article)):
            deliveries.append(
                QQDelivery(
                    article_id=article.id,
                    article_publish_attempt_id=attempt_id,
                    sequence=sequence,
                    media_path=None,
                    target_id=None,
                    source_tweet_id=None,
                    kind="article",
                    idempotency_key=f"article:{article.id}:{attempt_id}:{group}:{sequence}",
                    bot_name=bot.name,
                    bot_app_id=bot.app_id,
                    bot_version=bot.version,
                    target_name=group,
                    group_openid=group,
                    message_body=body,
                    status="queued",
                    attempts=0,
                    max_attempts=get_settings().qq_worker_max_attempts,
                    next_attempt_at=now,
                )
            )
            sequence += 1
        for path in image_paths:
            deliveries.append(
                QQDelivery(
                    article_id=article.id,
                    article_publish_attempt_id=attempt_id,
                    sequence=sequence,
                    media_path=str(path),
                    target_id=None,
                    source_tweet_id=None,
                    kind="article",
                    idempotency_key=f"article:{article.id}:{attempt_id}:{group}:{sequence}",
                    bot_name=bot.name,
                    bot_app_id=bot.app_id,
                    bot_version=bot.version,
                    target_name=group,
                    group_openid=group,
                    message_body="",
                    status="queued",
                    attempts=0,
                    max_attempts=get_settings().qq_worker_max_attempts,
                    next_attempt_at=now,
                )
            )
            sequence += 1
    article.publish_status = "queued"
    article.publish_channel = "qq"
    article.publish_attempt_id = attempt_id
    article.publish_error = None
    article.published_at = None
    db.add(
        ArticlePublishAttempt(
            attempt_id=attempt_id,
            article_id=article.id,
            channel="qq",
            status="queued",
            target_summary=f"{bot.name} · {len(joined)} 个群",
            delivery_count=len(deliveries),
            started_at=now,
        )
    )
    db.add_all(deliveries)
    await db.commit()
    ids = [row.id for row in deliveries]
    await enqueue_qq_delivery_ids(redis, ids)
    return ArticlePublishAccepted(
        message=f"文章已拆分为 {len(ids)} 条 QQ 消息并进入投递队列",
        channel="qq",
        publish_status="queued",
        delivery_ids=ids,
    )


@router.post("/{article_id}/publish", response_model=ArticlePublishAccepted)
async def publish_article(
    article_id: int,
    payload: ArticlePublishCreate,
    db: DbSession,
    redis: RedisClient,
    admin: CurrentAdmin,
) -> ArticlePublishAccepted:
    article = await db.scalar(select(AIDraft).where(AIDraft.id == article_id).with_for_update())
    if article is None:
        raise APIError(404, "article_not_found", "文章不存在")
    if article.publish_status == "queued":
        raise APIError(409, "article_publish_in_progress", "文章正在推送，请等待本次推送完成")
    if payload.channel == "qq":
        return await _publish_to_qq(article, payload, db, redis, admin)

    source = await _source_screenshot_path(db, article)
    if not article.images and source is None:
        raise APIError(422, "xhs_images_required", "小红书图文推送至少需要一张图片")
    if len(article.images or []) + (source is not None) > 18:
        raise APIError(422, "too_many_article_images", "含原帖截图在内，小红书最多发布 18 张图片")
    if len(article.title) > XHS_NOTE_TITLE_MAX_LENGTH:
        raise APIError(
            422,
            "xhs_title_too_long",
            f"小红书标题不能超过 {XHS_NOTE_TITLE_MAX_LENGTH} 个字符",
        )
    if len(article.content) > XHS_NOTE_CONTENT_MAX_LENGTH:
        raise APIError(
            422,
            "xhs_content_too_long",
            f"小红书正文不能超过 {XHS_NOTE_CONTENT_MAX_LENGTH} 个字符",
        )
    if not await has_xhs_credentials(db, admin_id=admin.id):
        raise APIError(409, "xhs_credentials_not_configured", "请先保存小红书登录态")
    paths = []
    for image in article.images or []:
        path = await asyncio.to_thread(article_image_path, image, admin_id=admin.id)
        if path is None:
            raise APIError(400, "article_image_invalid", "文章包含不存在的图片")
        paths.append(str(path))
    source_copy = await _snapshot_source_screenshot(article, source, admin)
    if source_copy is not None:
        paths.insert(0, str(source_copy))
    attempt_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    article.publish_status = "queued"
    article.publish_channel = "xhs"
    article.publish_attempt_id = attempt_id
    article.publish_error = None
    article.published_at = None
    attempt = ArticlePublishAttempt(
        attempt_id=attempt_id,
        article_id=article.id,
        channel="xhs",
        status="queued",
        target_summary="当前小红书账号",
        delivery_count=1,
        started_at=now,
    )
    db.add(attempt)
    await db.commit()
    try:
        await asyncio.to_thread(clear_verification_image, admin.id)
        await submit_xhs_job(
            redis,
            operation="post",
            admin_id=admin.id,
            payload={"title": article.title, "content": article.content, "images": paths},
            timeout_seconds=get_settings().xhs_job_timeout_seconds + 5,
        )
    except Exception as exc:
        logger.exception(
            "Article Xiaohongshu publish failed",
            extra={"article_id": article.id, "publish_attempt_id": attempt_id},
        )
        article.publish_status = "failed"
        article.publish_error = str(exc)[:2000]
        attempt.status = "failed"
        attempt.error = str(exc)[:2000]
        attempt.completed_at = datetime.now(UTC)
        await db.commit()
        if isinstance(exc, XHSWorkerUnavailableError):
            status_code = 503
        elif isinstance(exc, XHSJobTimeoutError):
            status_code = 504
        else:
            status_code = 502
        raise APIError(status_code, "article_xhs_publish_failed", str(exc)) from None
    article.publish_status = "published"
    article.published_at = datetime.now(UTC)
    attempt.status = "published"
    attempt.error = None
    attempt.completed_at = article.published_at
    await db.commit()
    return ArticlePublishAccepted(
        message="文章已成功推送到小红书",
        channel="xhs",
        publish_status="published",
    )
