from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.api.routes.articles import create_article, delete_article, update_article
from app.models.ai import AIDraft
from app.schemas.article import ArticleCreate, ArticlePatch


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
        ArticleCreate(title="  手动文章  ", content="  正文内容  ", status="draft"),
        session,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert result.article_source == "user"
    assert result.title == "手动文章"
    assert result.content == "正文内容"
    assert result.job_id is None
    assert result.source_tweet_id is None


async def test_article_update_increments_revision() -> None:
    now = datetime.now(UTC)
    article = AIDraft(
        id=7,
        job_id=4,
        source_tweet_id=3,
        article_source="ai",
        title="旧标题",
        content="旧正文",
        status="draft",
        revision=2,
        created_at=now,
        updated_at=now,
    )
    session = MutationSession(article)

    result = await update_article(
        7,
        ArticlePatch(title="新标题", status="approved", revision=2),
        session,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
    )

    assert result.title == "新标题"
    assert result.article_source == "ai"
    assert result.status == "approved"
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
