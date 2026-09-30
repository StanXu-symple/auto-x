from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.models.qq import QQNotificationTarget
from app.models.qq_placeholder import QQPlaceholder
from app.schemas.common import MessageResponse, Page
from app.schemas.qq import normalize_message_template
from app.schemas.qq_placeholder import (
    FIELD_LABELS,
    SOURCE_FIELDS,
    QQPlaceholderField,
    QQPlaceholderOut,
    QQPlaceholderWrite,
)
from app.services.qq_placeholders import parse_template_fields

router = APIRouter(prefix="/qq/placeholders", tags=["QQ Notifications"])


@router.get("/fields", response_model=list[QQPlaceholderField])
async def list_fields(_: CurrentAdmin):
    return [
        QQPlaceholderField(
            value=field,
            label=FIELD_LABELS.get(field, field),
            category="系统生成" if field in {"url", "title"} else "内容流",
        )
        for field in sorted(SOURCE_FIELDS)
    ]


@router.get("", response_model=Page[QQPlaceholderOut] | list[QQPlaceholderOut])
async def list_placeholders(
    db: DbSession,
    _: CurrentAdmin,
    page: int | None = Query(default=None, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
):
    statement = select(QQPlaceholder).order_by(QQPlaceholder.id)
    if page is not None:
        statement = statement.offset((page - 1) * page_size).limit(page_size)
    rows = list(await db.scalars(statement))
    if page is not None:
        total = int(await db.scalar(select(func.count(QQPlaceholder.id))) or 0)
        return Page(items=rows, total=total, page=page, page_size=page_size)
    return rows


async def ensure_no_variable_collision(db: DbSession, placeholder: str):
    key = placeholder[1:-1]
    targets = await db.scalars(select(QQNotificationTarget))
    if any(key in (target.template_variables or {}) for target in targets):
        raise APIError(409, "qq_placeholder_collision", "该名称已被群目标的自定义变量使用")


async def persist(db: DbSession, row: QQPlaceholder):
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "qq_placeholder_exists", "占位符已存在，请使用其他名称") from None
    await db.refresh(row)
    return row


@router.post("", response_model=QQPlaceholderOut, status_code=201)
async def create_placeholder(payload: QQPlaceholderWrite, db: DbSession, _: CurrentAdmin):
    await ensure_no_variable_collision(db, payload.placeholder)
    row = QQPlaceholder(**payload.model_dump())
    db.add(row)
    return await persist(db, row)


@router.patch("/{placeholder_id}", response_model=QQPlaceholderOut)
async def update_placeholder(
    placeholder_id: int, payload: QQPlaceholderWrite, db: DbSession, _: CurrentAdmin
):
    row = await db.get(QQPlaceholder, placeholder_id)
    if row is None:
        raise APIError(404, "qq_placeholder_not_found", "占位符不存在")
    if payload.placeholder != row.placeholder:
        # Keep defaults usable by default templates even without a saved target.
        if row.placeholder in {
            "{author}",
            "{username}",
            "{text}",
            "{url}",
            "{posted_at}",
            "{title}",
        }:
            raise APIError(409, "qq_placeholder_in_use", "默认占位符不可改名，可修改原始字段映射")
        targets = await db.scalars(select(QQNotificationTarget))
        old = row.placeholder[1:-1]
        for target in targets:
            template = normalize_message_template(target.message_template, {old: ""})
            if old in parse_template_fields(template):
                raise APIError(409, "qq_placeholder_in_use", "占位符被群目标使用，请先修改对应模板")
        await ensure_no_variable_collision(db, payload.placeholder)
    row.placeholder = payload.placeholder
    row.source_field = payload.source_field
    return await persist(db, row)


@router.delete("/{placeholder_id}", response_model=MessageResponse)
async def delete_placeholder(placeholder_id: int, db: DbSession, _: CurrentAdmin):
    row = await db.get(QQPlaceholder, placeholder_id, with_for_update=True)
    if row is None:
        raise APIError(404, "qq_placeholder_not_found", "占位符不存在或已删除")
    key = row.placeholder[1:-1]
    targets = await db.scalars(select(QQNotificationTarget).order_by(QQNotificationTarget.id))
    used_by = []
    for target in targets:
        template = normalize_message_template(target.message_template, {key: ""})
        try:
            referenced = key in parse_template_fields(template)
        except ValueError:
            # Legacy invalid templates must not crash deletion or hide a reference.
            referenced = row.placeholder in template
        if referenced:
            used_by.append(
                {
                    "id": target.id,
                    "name": target.name,
                    "group_openid": target.group_openid,
                    "is_enabled": target.is_enabled,
                }
            )
    if used_by:
        raise APIError(
            409,
            "qq_placeholder_in_use",
            "无法删除，请先删除以下使用此占位符的群目标",
            details={"targets": used_by},
        )
    await db.delete(row)
    await db.commit()
    return MessageResponse(message="占位符已删除")
