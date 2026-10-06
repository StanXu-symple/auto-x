from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.api.errors import APIError
from app.api.routes.articles import (
    _publish_filter,
    _source_url,
    create_article,
    delete_article,
    list_articles,
    publish_article,
    update_article,
)
from app.models.ai import AIDraft, AIGenerationJob
from app.models.ai_publish import AIPublishDispatch
from app.models.tweet import Tweet
from app.schemas.article import ArticleCreate, ArticlePatch, ArticlePublishCreate
from app.services.xhs_limits import (
    XHS_NOTE_CONTENT_MAX_LENGTH,
    XHS_NOTE_TITLE_MAX_LENGTH,
)


class MutationSession:
    def __init__(self, article: AIDraft | None = None) -> None:
        self.article = article
        self.deleted = False

    def add(self, article: AIDraft) -> None:
        self.article = article

    async def scalar(self, _statement):
        return self.article

    async def scalars(self, _statement):
        return []

    async def execute(self, _statement):
        return []

    async def get(self, _model, _article_id):
        return self.article

    async def commit(self) -> None:
        if self.article is not None and self.article.id is None:
            self.article.id = 1
        if self.article is not None:
            now = datetime.now(UTC)
            self.article.created_at = self.article.created_at or now
            self.article.updated_at = now

    async def refresh(self, _article: AIDraft) -> None:
        return None

    async def delete(self, _article: AIDraft) -> None:
        self.deleted = True


async def test_manual_article_is_created_with_user_source() -> None:
    session = MutationSession()
    result = await create_article(
        ArticleCreate(title="  手动文章  ", content="  正文内容  "),
        session,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert result.article_source == "user"
    assert result.title == "手动文章"
    assert result.content == "正文内容"
    assert result.job_id is None
    assert result.source_tweet_id is None
    assert result.source_url is None
    assert result.images == []
    assert result.publish_status == "unpublished"


async def test_article_update_increments_revision() -> None:
    now = datetime.now(UTC)
    article = AIDraft(
        id=7,
        job_id=4,
        source_tweet_id=3,
        article_source="ai",
        title="旧标题",
        content="旧正文",
        revision=2,
        created_at=now,
        updated_at=now,
    )
    session = MutationSession(article)
    session.scalar = AsyncMock(side_effect=[article, None])
    session.get = AsyncMock(
        return_value=AIGenerationJob(
            id=4,
            status="succeeded",
            task_snapshot={},
            publish_outbox_initialized_at=now,
        )
    )
    session.execute = AsyncMock(
        return_value=[
            (Tweet(id=3, tweet_id="1840000000000000012", raw_payload={}), "original", None)
        ]
    )

    result = await update_article(
        7,
        ArticlePatch(title="新标题", revision=2),
        session,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert result.title == "新标题"
    assert result.article_source == "ai"
    assert result.revision == 3
    assert result.source_url == "https://x.com/original/status/1840000000000000012"


async def test_article_list_resolves_sources_in_one_batch() -> None:
    now = datetime.now(UTC)
    articles = [
        AIDraft(
            id=index,
            job_id=index if source_id else None,
            article_source="ai" if source_id else "user",
            source_tweet_id=source_id,
            title="文章",
            content="正文",
            revision=1,
            created_at=now,
            updated_at=now,
        )
        for index, source_id in enumerate([3, 3, None, 4], start=1)
    ]
    session = AsyncMock()
    session.scalar.return_value = 4
    dispatches = [
        AIPublishDispatch(
            id=10,
            job_id=1,
            draft_id=1,
            channel="xhs",
            status="published",
            attempts=1,
            next_attempt_at=now,
            updated_at=now,
        ),
        AIPublishDispatch(
            id=11,
            job_id=1,
            draft_id=1,
            channel="qq",
            status="failed",
            attempts=1,
            last_error="群已退出",
            next_attempt_at=now,
            updated_at=now,
        ),
    ]
    session.scalars.side_effect = [articles, dispatches]
    session.execute.return_value = [
        (
            Tweet(
                id=3,
                tweet_id="1840000000000000012",
                raw_payload={
                    "source_url": "https://twitter.com/actual_author/status/1840000000000000012"
                },
            ),
            "monitored",
            None,
        )
    ]

    result = await list_articles(
        session,
        None,
        page=1,
        page_size=20,
        keyword=None,  # type: ignore[arg-type]
    )

    assert [article.source_url for article in result.items] == [
        "https://x.com/actual_author/status/1840000000000000012",
        "https://x.com/actual_author/status/1840000000000000012",
        None,
        None,
    ]
    assert result.total == 4
    assert [(item.channel, item.status) for item in result.items[0].auto_publish_dispatches] == [
        ("xhs", "published"),
        ("qq", "failed"),
    ]
    assert result.items[0].auto_publish_dispatches[1].last_error == "群已退出"
    assert all(not item.auto_publish_dispatches for item in result.items[1:])
    assert session.scalars.await_count == 2
    session.execute.assert_awaited_once()


def test_article_status_filter_includes_independent_auto_publish_results() -> None:
    for status, expected in (
        ("queued", ("pending", "retry_wait", "dispatching", "accepted")),
        ("published", ("published",)),
        ("failed", ("failed", "uncertain")),
    ):
        query = select(AIDraft.id).where(_publish_filter(status))
        sql = str(
            query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        assert "EXISTS" in sql
        assert "ai_publish_dispatches.draft_id = ai_drafts.id" in sql
        assert all(f"'{item}'" in sql for item in expected)
    unpublished = select(AIDraft.id).where(_publish_filter("unpublished"))
    sql = str(unpublished.compile(dialect=postgresql.dialect()))
    assert "NOT (EXISTS" in sql


@pytest.mark.parametrize(
    "source_url",
    [
        "https://evil.example/author/status/1840000000000000012",
        "https://user:password@x.com/author/status/1840000000000000012",
        "https://x.com/author/status/3",
        "http://x.com/author/status/1840000000000000012",
    ],
)
def test_article_source_rejects_untrusted_urls(source_url: str) -> None:
    tweet = Tweet(tweet_id="1840000000000000012", raw_payload={"source_url": source_url})

    assert _source_url(tweet, "monitored") == "https://x.com/monitored/status/1840000000000000012"
    assert _source_url(tweet, "invalid/username") is None


async def test_article_delete_removes_only_the_article() -> None:
    article = AIDraft(id=9, article_source="user", title="待删除", content="正文", revision=1)
    session = MutationSession(article)

    result = await delete_article(
        9,
        session,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert result.message == "文章已删除"
    assert session.deleted is True


@pytest.mark.parametrize("operation", ["edit", "delete"])
async def test_article_mutation_rejects_active_auto_publish(operation: str) -> None:
    article = AIDraft(
        id=12, job_id=7, article_source="ai", title="自动文章", content="正文",
        publish_status="unpublished", revision=1,
    )
    session = AsyncMock()
    session.scalar.side_effect = [article, 8]
    if operation == "edit":
        action = update_article(
            12, ArticlePatch(title="新标题", revision=1), session, None
        )
    else:
        action = delete_article(12, session, None)
    with pytest.raises(APIError) as error:
        await action
    assert error.value.status_code == 409
    assert error.value.code == "article_auto_publish_in_progress"
    assert article.title == "自动文章"
    locked_article_query = session.scalar.call_args_list[0].args[0]
    locked_article_sql = str(locked_article_query.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in locked_article_sql
    active_query = session.scalar.call_args_list[1].args[0]
    sql = str(
        active_query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert all(
        f"'{status}'" in sql
        for status in ("pending", "retry_wait", "dispatching", "accepted")
    )
    session.commit.assert_not_awaited()
    session.delete.assert_not_awaited()


@pytest.mark.parametrize("operation", ["edit", "delete"])
async def test_article_mutation_rejects_uninitialized_auto_publish(operation: str) -> None:
    article = AIDraft(
        id=12, job_id=7, article_source="ai", title="自动文章", content="正文",
        publish_status="unpublished", revision=1,
    )
    job = AIGenerationJob(
        id=7, status="succeeded", task_snapshot={"auto_publish_channels": ["xhs"]},
        publish_outbox_initialized_at=None,
    )
    session = AsyncMock()
    session.scalar.side_effect = [article, None]
    session.get.return_value = job
    if operation == "edit":
        action = update_article(
            12, ArticlePatch(title="新标题", revision=1), session, None
        )
    else:
        action = delete_article(12, session, None)
    with pytest.raises(APIError) as error:
        await action
    assert error.value.status_code == 409
    assert error.value.code == "article_auto_publish_pending"
    session.commit.assert_not_awaited()
    session.delete.assert_not_awaited()


def test_article_payload_rejects_blank_content_and_empty_patch() -> None:
    with pytest.raises(ValidationError):
        ArticleCreate(title="标题", content="   ")
    with pytest.raises(ValidationError):
        ArticlePatch(revision=1)


def test_article_payload_deduplicates_publish_groups() -> None:
    payload = ArticlePublishCreate(
        channel="qq", bot_id=1, group_openids=[" group-a ", "group-a", "group-b"]
    )

    assert payload.group_openids == ["group-a", "group-b"]


async def test_published_article_can_be_published_again(monkeypatch) -> None:
    article = AIDraft(
        id=11,
        article_source="user",
        title="可重复推送",
        content="正文",
        publish_status="published",
        revision=1,
    )
    session = MutationSession(article)
    expected = object()
    publish_to_qq = AsyncMock(return_value=expected)
    monkeypatch.setattr("app.api.routes.articles._publish_to_qq", publish_to_qq)

    result = await publish_article(
        11,
        ArticlePublishCreate(channel="qq", bot_id=1, group_openids=["group"]),
        session,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert result is expected
    publish_to_qq.assert_awaited_once()


async def test_article_cannot_start_parallel_publish() -> None:
    article = AIDraft(
        id=12,
        article_source="user",
        title="正在推送",
        content="正文",
        publish_status="queued",
        revision=1,
    )

    with pytest.raises(APIError) as exc_info:
        await publish_article(
            12,
            ArticlePublishCreate(channel="qq", bot_id=1, group_openids=["group"]),
            MutationSession(article),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 409


@pytest.mark.parametrize("status", ["pending", "retry_wait", "accepted", "published", "uncertain"])
async def test_manual_publish_rejects_same_channel_auto_dispatch(status: str) -> None:
    article = AIDraft(
        id=12,
        job_id=7,
        article_source="ai",
        title="自动文章",
        content="正文",
        publish_status="unpublished",
        revision=1,
    )
    dispatch = AIPublishDispatch(id=8, draft_id=12, job_id=7, channel="qq", status=status)
    session = AsyncMock()
    session.scalar.side_effect = [article, dispatch]
    with pytest.raises(APIError) as exc_info:
        await publish_article(
            12,
            ArticlePublishCreate(channel="qq", bot_id=1, group_openids=["group"]),
            session,
            None,
            None,
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "article_auto_publish_conflict"
    assert exc_info.value.details == {"channel": "qq", "status": status}


async def test_manual_publish_blocks_uninitialized_auto_dispatch() -> None:
    article = AIDraft(id=12, job_id=7, article_source="ai", title="自动文章", content="正文")
    job = AIGenerationJob(id=7, status="succeeded", task_snapshot={"auto_publish_channels": ["qq"]})
    session = AsyncMock()
    session.scalar.side_effect = [article, None]
    session.get.return_value = job
    with pytest.raises(APIError) as exc_info:
        await publish_article(
            12,
            ArticlePublishCreate(channel="qq", bot_id=1, group_openids=["group"]),
            session,
            None,
            None,
        )
    assert exc_info.value.code == "article_auto_publish_pending"


async def test_manual_publish_can_retry_known_preflight_failure(monkeypatch) -> None:
    article = AIDraft(id=12, job_id=7, article_source="ai", title="自动文章", content="正文")
    dispatch = AIPublishDispatch(id=8, draft_id=12, job_id=7, channel="qq", status="failed")
    session = AsyncMock()
    session.scalar.side_effect = [article, dispatch]
    expected = object()
    publish_to_qq = AsyncMock(return_value=expected)
    monkeypatch.setattr("app.api.routes.articles._publish_to_qq", publish_to_qq)
    result = await publish_article(
        12,
        ArticlePublishCreate(channel="qq", bot_id=1, group_openids=["group"]),
        session,
        None,
        None,
    )
    assert result is expected
    publish_to_qq.assert_awaited_once()


async def test_manual_publish_blocks_failed_dispatch_that_reached_qq_outbox() -> None:
    article = AIDraft(id=12, job_id=7, article_source="ai", title="自动文章", content="正文")
    dispatch = AIPublishDispatch(
        id=8,
        draft_id=12,
        job_id=7,
        channel="qq",
        status="failed",
        article_publish_attempt_id="attempt-1",
    )
    session = AsyncMock()
    session.scalar.side_effect = [article, dispatch]
    with pytest.raises(APIError) as exc_info:
        await publish_article(
            12,
            ArticlePublishCreate(channel="qq", bot_id=1, group_openids=["group"]),
            session,
            None,
            None,
        )
    assert exc_info.value.code == "article_auto_publish_conflict"


@pytest.mark.parametrize(
    ("title", "content", "error_code"),
    (
        (
            "标" * (XHS_NOTE_TITLE_MAX_LENGTH + 1),
            "正文",
            "xhs_title_too_long",
        ),
        (
            "标题",
            "文" * (XHS_NOTE_CONTENT_MAX_LENGTH + 1),
            "xhs_content_too_long",
        ),
    ),
)
async def test_article_xhs_publish_rejects_platform_text_limit_before_queueing(
    title: str,
    content: str,
    error_code: str,
) -> None:
    article = AIDraft(
        id=13,
        article_source="ai",
        title=title,
        content=content,
        images=["1/image.png"],
        publish_status="unpublished",
        revision=1,
    )

    with pytest.raises(APIError) as exc_info:
        await publish_article(
            13,
            ArticlePublishCreate(channel="xhs"),
            MutationSession(article),  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
        )

    assert exc_info.value.code == error_code
    assert article.publish_status == "unpublished"
