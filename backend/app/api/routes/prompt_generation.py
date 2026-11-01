"""Queue prompt authoring on ai-worker; persistence still uses the Skill facade."""

from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.db.base import utcnow
from app.models.ai import AISetting
from app.models.ai_data_source import AIDataSource
from app.models.prompt_generation import PromptGeneration
from app.schemas.ai import PromptGenerationOut, PromptGenerationRequest

router = APIRouter(prefix="/ai/prompt-generations", tags=["Prompt Generation"])


@router.post("", response_model=PromptGenerationOut, status_code=202)
async def create_generation(payload: PromptGenerationRequest, db: DbSession, admin: CurrentAdmin):
    async def existing():
        row = await db.scalar(
            select(PromptGeneration).where(
                PromptGeneration.admin_id == admin.id,
                PromptGeneration.request_id == str(payload.request_id),
            )
        )
        if row and row.requirement != payload.requirement:
            raise APIError(409, "request_conflict", "同一请求编号不能用于不同需求")
        return row

    if row := await existing():
        return row
    setting = await db.get(AISetting, 1)
    source = await db.get(AIDataSource, 1)
    if not setting or not setting.enabled or not source:
        raise APIError(409, "ai_not_ready", "请先启用 AI 创作并配置 AI 数据源")
    if source.protocol != "openai_responses":
        raise APIError(409, "ai_protocol_unsupported", "提示词生成需要 Responses 协议的 AI 数据源")
    row = PromptGeneration(
        admin_id=admin.id,
        request_id=str(payload.request_id),
        requirement=payload.requirement,
        config_snapshot={
            "language": setting.language,
            "tone": setting.tone,
            "reasoning_effort": setting.reasoning_effort,
            "max_output_tokens": min(setting.max_output_tokens, 16000),
            "timeout_seconds": setting.request_timeout_seconds,
            "base_url": source.base_url,
            "model": source.model_name,
            "protocol": source.protocol,
        },
    )
    try:
        db.add(row)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if row := await existing():
            return row
        raise
    return row


async def owned_generation(db, generation_id, admin_id, *, lock=False):
    query = select(PromptGeneration).where(
        PromptGeneration.id == str(generation_id), PromptGeneration.admin_id == admin_id
    )
    row = await db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise APIError(404, "prompt_generation_not_found", "提示词生成记录不存在")
    return row


@router.get("/{generation_id}", response_model=PromptGenerationOut)
async def read_generation(generation_id: UUID, db: DbSession, admin: CurrentAdmin):
    return await owned_generation(db, generation_id, admin.id)


@router.post("/{generation_id}/cancel", response_model=PromptGenerationOut)
async def cancel_generation(generation_id: UUID, db: DbSession, admin: CurrentAdmin):
    row = await owned_generation(db, generation_id, admin.id, lock=True)
    if row.status in ("queued", "running"):
        row.status = "cancelled"
        row.claim_token = row.lease_expires_at = None
        row.updated_at = utcnow()
        await db.commit()
    return row
