from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.models.qq_message_template import QQMessageTemplate
from app.schemas.common import MessageResponse, Page
from app.schemas.qq_message_template import QQMessageTemplateOut, QQMessageTemplateWrite
from app.services.qq_placeholders import validate_target_template

router = APIRouter(prefix="/qq/message-templates", tags=["QQ Notifications"])


@router.get("", response_model=Page[QQMessageTemplateOut])
async def list_templates(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=15, ge=1, le=100),
    search: str | None = Query(default=None, max_length=100),
):
    conditions = (
        [QQMessageTemplate.name.contains(search.strip(), autoescape=True)] if search else []
    )
    total = int(await db.scalar(select(func.count(QQMessageTemplate.id)).where(*conditions)) or 0)
    rows = list(
        await db.scalars(
            select(QQMessageTemplate)
            .where(*conditions)
            .order_by(QQMessageTemplate.updated_at.desc(), QQMessageTemplate.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(items=rows, total=total, page=page, page_size=page_size)


async def get_row(db: DbSession, template_id: int) -> QQMessageTemplate:
    row = await db.get(QQMessageTemplate, template_id)
    if row is None:
        raise APIError(404, "qq_message_template_not_found", "消息模板不存在或已删除")
    return row


@router.get("/{template_id}", response_model=QQMessageTemplateOut)
async def get_template(template_id: int, db: DbSession, _: CurrentAdmin):
    row = await get_row(db, template_id)
    # A mapping may have been removed since this reusable template was saved.
    await validate_target_template(db, row.message_template, row.template_variables)
    return row


async def save_row(db: DbSession, row: QQMessageTemplate, payload: QQMessageTemplateWrite):
    template = await validate_target_template(
        db, payload.message_template, payload.template_variables
    )
    row.name = payload.name
    row.message_template = template
    row.template_variables = payload.template_variables
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(
            409, "qq_message_template_exists", "消息模板名称已存在，请使用其他名称"
        ) from None
    await db.refresh(row)
    return row


@router.post("", response_model=QQMessageTemplateOut, status_code=201)
async def create_template(payload: QQMessageTemplateWrite, db: DbSession, _: CurrentAdmin):
    return await save_row(db, QQMessageTemplate(), payload)


@router.put("/{template_id}", response_model=QQMessageTemplateOut)
async def update_template(
    template_id: int, payload: QQMessageTemplateWrite, db: DbSession, _: CurrentAdmin
):
    return await save_row(db, await get_row(db, template_id), payload)


@router.delete("/{template_id}", response_model=MessageResponse)
async def delete_template(template_id: int, db: DbSession, _: CurrentAdmin):
    # Targets keep their own copies, so removing a reusable template never changes delivery.
    await db.delete(await get_row(db, template_id))
    await db.commit()
    return MessageResponse(message="消息模板已删除")
