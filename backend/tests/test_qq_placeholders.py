from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.errors import APIError
from app.api.routes.qq_placeholders import create_placeholder, list_fields, update_placeholder
from app.db.base import Base
from app.models.qq import QQNotificationTarget
from app.models.qq_placeholder import QQPlaceholder
from app.schemas.qq_placeholder import DEFAULT_PLACEHOLDERS, QQPlaceholderWrite
from app.schemas.tweet import TweetOut
from app.services.qq_notifications import render_qq_message
from app.services.qq_placeholders import (
    load_placeholder_mappings,
    parse_template_fields,
    validate_target_template,
)


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda connection: Base.metadata.create_all(
                connection, tables=[QQPlaceholder.__table__, QQNotificationTarget.__table__]
            )
        )
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        session.add_all([QQPlaceholder(**row) for row in DEFAULT_PLACEHOLDERS])
        await session.commit()
        yield session
    await engine.dispose()


async def test_catalog_covers_every_tweet_api_field():
    fields = await list_fields(object())
    assert {field.value for field in fields if field.category == "内容流"} == set(
        TweetOut.model_fields
    )
    assert {field.value for field in fields if field.category == "系统生成"} == {"url", "title"}


@pytest.mark.parametrize(
    "placeholder,source",
    [("{bad.name}", "text"), ("text", "text"), ("{{text}}", "text"), ("{test}", "missing")],
)
def test_rejects_invalid_inputs(placeholder, source):
    with pytest.raises(ValidationError):
        QQPlaceholderWrite(placeholder=placeholder, source_field=source)


@pytest.mark.parametrize("template", ["{text.x}", "{text[0]}", "{text!r}", "{text:>20}", "{"])
def test_rejects_expression_syntax(template):
    with pytest.raises(ValueError):
        parse_template_fields(template)


async def test_create_edit_mapping_changes_actual_rendering(db):
    row = await create_placeholder(
        QQPlaceholderWrite(placeholder="{likes}", source_field="like_count"), db, object()
    )
    template = await validate_target_template(db, "{{likes}} {author} {text}", {})
    assert template == "{likes} {author} {text}"
    tweet = SimpleNamespace(
        like_count=12, reply_count=7, text="内容", tweet_id="123", posted_at=datetime.now(UTC)
    )
    user = SimpleNamespace(username="alice", display_name="昵称")
    result = render_qq_message(
        template, tweet=tweet, user=user, placeholder_mappings=await load_placeholder_mappings(db)
    )
    assert result == "12 昵称 内容"
    await update_placeholder(
        row.id, QQPlaceholderWrite(placeholder="{likes}", source_field="reply_count"), db, object()
    )
    result = render_qq_message(
        template, tweet=tweet, user=user, placeholder_mappings=await load_placeholder_mappings(db)
    )
    assert result == "7 昵称 内容"


async def test_defaults_seeded_and_remappable(db):
    mappings = await load_placeholder_mappings(db)
    assert len(mappings) == 6
    row = await db.scalar(select(QQPlaceholder).where(QQPlaceholder.placeholder == "{author}"))
    with pytest.raises(APIError, match="默认占位符不可改名"):
        await update_placeholder(
            row.id, QQPlaceholderWrite(placeholder="{name}", source_field="text"), db, object()
        )
    await update_placeholder(
        row.id, QQPlaceholderWrite(placeholder="{author}", source_field="text"), db, object()
    )
    assert (await load_placeholder_mappings(db))["author"] == "text"


async def test_duplicate_placeholder_and_unknown_template_rejected(db):
    with pytest.raises(APIError) as error:
        await create_placeholder(
            QQPlaceholderWrite(placeholder="{text}", source_field="text"), db, object()
        )
    assert error.value.status_code == 409
    with pytest.raises(APIError) as error:
        await validate_target_template(db, "{not_configured}", {})
    assert error.value.status_code == 422


async def test_renaming_used_placeholder_and_custom_collision_rejected(db):
    row = await create_placeholder(
        QQPlaceholderWrite(placeholder="{likes}", source_field="like_count"), db, object()
    )
    db.add(
        QQNotificationTarget(
            bot_id=1,
            name="test",
            group_openid="abc",
            message_template="{{likes}}",
            template_variables={"topic": "科技"},
        )
    )
    await db.commit()
    with pytest.raises(APIError, match="被群目标使用"):
        await update_placeholder(
            row.id,
            QQPlaceholderWrite(placeholder="{count}", source_field="like_count"),
            db,
            object(),
        )
    with pytest.raises(APIError, match="自定义变量使用"):
        await create_placeholder(
            QQPlaceholderWrite(placeholder="{topic}", source_field="text"), db, object()
        )
    with pytest.raises(APIError, match="不能覆盖"):
        await validate_target_template(db, "{likes}", {"likes": "override"})


def test_json_null_zero_dates_and_generated_fields_render():
    tweet = SimpleNamespace(
        text="内容",
        like_count=0,
        lang=None,
        entities={"话题": ["测试"]},
        tweet_id="123",
        posted_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    user = SimpleNamespace(username="alice", display_name=None)
    mappings = {
        "name": "display_name",
        "count": "like_count",
        "language": "lang",
        "json": "entities",
        "time": "posted_at",
        "link": "url",
        "heading": "title",
    }
    result = render_qq_message(
        "{name}|{count}|{language}|{json}|{time}|{link}|{heading}",
        tweet=tweet,
        user=user,
        placeholder_mappings=mappings,
    )
    assert (
        result
        == 'alice|0||{"话题": ["测试"]}|2026-09-30 00:00:00|'
        'https://x.com/alice/status/123|【X Sentinel】内容推送'
    )
