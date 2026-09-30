from __future__ import annotations

import json
import re
from datetime import datetime
from string import Formatter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import APIError
from app.core.time import as_utc
from app.models.monitored_user import MonitoredUser
from app.models.qq_placeholder import QQPlaceholder
from app.models.tweet import Tweet
from app.schemas.qq_placeholder import DEFAULT_PLACEHOLDERS, SOURCE_FIELDS


def parse_template_fields(template: str) -> set[str]:
    fields = set()
    for _, field, spec, conversion in Formatter().parse(template):
        if field is None:
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field) or spec or conversion:
            raise ValueError("占位符必须为简单字段名，不支持属性访问或格式表达式")
        fields.add(field)
    return fields


async def load_placeholder_mappings(session: AsyncSession) -> dict[str, str]:
    rows = await session.scalars(select(QQPlaceholder).order_by(QQPlaceholder.id))
    return {row.placeholder[1:-1]: row.source_field for row in rows}


async def validate_target_template(
    session: AsyncSession, template: str, variables: dict[str, str] | None
) -> str:
    from app.schemas.qq import normalize_message_template

    mappings = await load_placeholder_mappings(session)
    variables = variables or {}
    if set(variables) & set(mappings):
        raise APIError(422, "qq_template_collision", "自定义变量不能覆盖已配置占位符")
    template = normalize_message_template(template, {**mappings, **variables})
    try:
        unknown = parse_template_fields(template) - set(mappings) - set(variables)
    except ValueError as exc:
        raise APIError(422, "qq_template_invalid", str(exc)) from None
    if unknown:
        raise APIError(422, "qq_template_unknown", f"未配置占位符：{', '.join(sorted(unknown))}")
    return template


def placeholder_values(
    mappings: dict[str, str] | None, tweet: Tweet, user: MonitoredUser, title: str
) -> dict[str, str]:
    # Fallback keeps the pure renderer usable; production callers load the DB mappings.
    if mappings is None:
        mappings = {row["placeholder"][1:-1]: row["source_field"] for row in DEFAULT_PLACEHOLDERS}
    values = {}
    for name, field in mappings.items():
        if field not in SOURCE_FIELDS:
            raise ValueError(f"Invalid placeholder source: {field}")
        if field == "display_name":
            value = user.display_name or user.username
        elif field == "username":
            value = user.username
        elif field == "url":
            value = f"https://x.com/{user.username}/status/{tweet.tweet_id}"
        elif field == "title":
            value = title
        else:
            value = getattr(tweet, field, None)
        if isinstance(value, datetime):
            value = as_utc(value).strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        values[name] = "" if value is None else str(value)
    return values
