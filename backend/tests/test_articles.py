from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.api.errors import APIError
from app.api.routes.articles import create_article, delete_article, publish_article, update_article
from app.models.ai import AIDraft
from app.schemas.article import ArticleCreate, ArticlePatch, ArticlePublishCreate


class MutationSession:
    def __init__(self, article: AIDraft | None = None) -> None:
        self.article = article
        self.deleted = False

    def add(self, article: AIDraft) -> None:
        self.article = article

    async def scalar(self, _statement):
        return self.article

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

    result = await update_article(
        7,
        ArticlePatch(title="新标题", revision=2),
        session,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert result.title == "新标题"
    assert result.article_source == "ai"
    assert result.revision == 3


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
