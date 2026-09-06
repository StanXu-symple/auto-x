from fastapi import APIRouter, Query, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.models.ai import AIDraft
from app.schemas.article import (
    ArticleCreate,
    ArticleOut,
    ArticlePatch,
    ArticleSource,
    ArticleStatus,
)
from app.schemas.common import MessageResponse, Page

router = APIRouter(prefix="/articles", tags=["Article Management"])


def _article_out(article: AIDraft) -> ArticleOut:
    return ArticleOut(
        id=article.id,
        job_id=article.job_id,
        source_tweet_id=article.source_tweet_id,
        article_source=article.article_source,
        title=article.title,
        content=article.content,
        excerpt=article.excerpt,
        status=article.status,
        revision=article.revision,
        created_at=article.created_at,
        updated_at=article.updated_at,
    )


@router.get("", response_model=Page[ArticleOut])
async def list_articles(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None, max_length=200),
    article_source: ArticleSource | None = None,
    article_status: ArticleStatus | None = Query(default=None, alias="status"),
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
    if article_status:
        conditions.append(AIDraft.status == article_status)

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
    return Page(
        items=[_article_out(article) for article in articles],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=ArticleOut, status_code=status.HTTP_201_CREATED)
async def create_article(
    payload: ArticleCreate, db: DbSession, _: CurrentAdmin
) -> ArticleOut:
    article = AIDraft(
        job_id=None,
        source_tweet_id=None,
        article_source="user",
        title=payload.title,
        content=payload.content,
        excerpt=payload.excerpt,
        status=payload.status,
        draft_metadata=None,
        revision=1,
    )
    db.add(article)
    await db.commit()
    await db.refresh(article)
    return _article_out(article)


@router.patch("/{article_id}", response_model=ArticleOut)
async def update_article(
    article_id: int,
    payload: ArticlePatch,
    db: DbSession,
    _: CurrentAdmin,
) -> ArticleOut:
    article = await db.scalar(
        select(AIDraft).where(AIDraft.id == article_id).with_for_update()
    )
    if article is None:
        raise APIError(404, "article_not_found", "文章不存在")
    if article.revision != payload.revision:
        raise APIError(
            409,
            "article_revision_conflict",
            "文章已被其他请求修改，请刷新后重试",
            {"current_revision": article.revision},
        )
    for key, value in payload.model_dump(exclude_unset=True, exclude={"revision"}).items():
        setattr(article, key, value)
    article.revision += 1
    await db.commit()
    await db.refresh(article)
    return _article_out(article)


@router.delete("/{article_id}", response_model=MessageResponse)
async def delete_article(
    article_id: int, db: DbSession, _: CurrentAdmin
) -> MessageResponse:
    article = await db.get(AIDraft, article_id)
    if article is None:
        raise APIError(404, "article_not_found", "文章不存在")
    await db.delete(article)
    await db.commit()
    return MessageResponse(message="文章已删除")
