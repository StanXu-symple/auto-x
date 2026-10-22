from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.db.base import utcnow
from app.models.agent_chat import AgentConversation, AgentTurn
from app.models.ai import AIDraft, AISetting
from app.schemas.agent_chat import (
    AgentConversationDetail,
    AgentConversationOut,
    AgentTurnOut,
    SendAgentMessage,
)
from app.schemas.common import Page
from app.services.skill_client import get_skill_client

router = APIRouter(prefix="/agent-chat", tags=["agent-chat"])
ACTIVE = ("queued", "running")


async def owned_conversation(
    db: AsyncSession, conversation_id: str, admin_id: int, *, lock: bool = False
) -> AgentConversation:
    query = select(AgentConversation).where(
        AgentConversation.id == conversation_id, AgentConversation.admin_id == admin_id
    )
    if lock:
        query = query.with_for_update()
    conversation = await db.scalar(query)
    if conversation is None:
        raise APIError(404, "conversation_not_found", "对话不存在")
    return conversation


async def owned_turn(
    db: AsyncSession, conversation_id: str, turn_id: str, admin_id: int
) -> tuple[AgentConversation, AgentTurn]:
    conversation = await owned_conversation(db, conversation_id, admin_id, lock=True)
    turn = await db.scalar(
        select(AgentTurn)
        .where(AgentTurn.id == turn_id, AgentTurn.conversation_id == conversation_id)
        .with_for_update()
    )
    if turn is None:
        raise APIError(404, "turn_not_found", "消息不存在")
    return conversation, turn


async def generation_config(db: AsyncSession) -> dict:
    setting = await db.get(AISetting, 1)
    if setting is None or not setting.enabled:
        raise APIError(409, "ai_disabled", "请先在 AI 创作设置中启用 AI，并配置 AI 数据源")
    return {
        "language": setting.language,
        "tone": setting.tone,
        "reasoning_effort": setting.reasoning_effort,
        "max_output_tokens": setting.max_output_tokens,
        "timeout_seconds": setting.request_timeout_seconds,
    }


@router.get("/conversations", response_model=Page[AgentConversationOut])
async def list_conversations(
    db: DbSession,
    admin: CurrentAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
) -> Page[AgentConversationOut]:
    condition = AgentConversation.admin_id == admin.id
    total = await db.scalar(select(func.count()).select_from(AgentConversation).where(condition))
    rows = await db.scalars(
        select(AgentConversation)
        .where(condition)
        .order_by(AgentConversation.updated_at.desc(), AgentConversation.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(items=list(rows), total=total or 0, page=page, page_size=page_size)


@router.post("/conversations", response_model=AgentConversationOut, status_code=201)
async def create_conversation(db: DbSession, admin: CurrentAdmin) -> AgentConversation:
    conversation = AgentConversation(admin_id=admin.id)
    db.add(conversation)
    await db.commit()
    return conversation


@router.get("/conversations/{conversation_id}", response_model=AgentConversationDetail)
async def get_conversation(
    conversation_id: UUID, db: DbSession, admin: CurrentAdmin
) -> AgentConversationDetail:
    conversation = await owned_conversation(db, str(conversation_id), admin.id)
    turns = await db.scalars(
        select(AgentTurn)
        .where(AgentTurn.conversation_id == conversation.id)
        .order_by(AgentTurn.sequence)
    )
    return AgentConversationDetail(
        **AgentConversationOut.model_validate(conversation).model_dump(),
        turns=[AgentTurnOut.model_validate(turn) for turn in turns],
    )


@router.post(
    "/conversations/{conversation_id}/messages", response_model=AgentTurnOut, status_code=202
)
async def send_message(
    conversation_id: UUID, payload: SendAgentMessage, db: DbSession, admin: CurrentAdmin
) -> AgentTurn:
    conversation = await owned_conversation(db, str(conversation_id), admin.id, lock=True)
    previous = await db.scalar(
        select(AgentTurn).where(
            AgentTurn.conversation_id == conversation.id,
            AgentTurn.request_id == str(payload.request_id),
        )
    )
    if previous is not None:
        if (
            previous.content != payload.content
            or [item["id"] for item in previous.skill_snapshot] != payload.skill_ids
        ):
            raise APIError(409, "request_conflict", "同一请求编号不能用于不同消息")
        return previous
    active = await db.scalar(
        select(AgentTurn.id).where(
            AgentTurn.conversation_id == conversation.id, AgentTurn.status.in_(ACTIVE)
        )
    )
    if active:
        raise APIError(409, "conversation_busy", "请等待当前回复完成，或先取消生成")
    sequence = (
        await db.scalar(
            select(func.max(AgentTurn.sequence)).where(AgentTurn.conversation_id == conversation.id)
        )
        or 0
    ) + 1
    if sequence > 100:
        raise APIError(409, "conversation_full", "本对话已达 100 条消息，请新建对话")
    config = await generation_config(db)
    skills = await get_skill_client().resolve(payload.skill_ids) if payload.skill_ids else []
    if [skill.id for skill in skills] != payload.skill_ids or any(
        not skill.is_active for skill in skills
    ):
        raise APIError(422, "invalid_references", "所选提示词或 Skill 已停用或不存在，请重新选择")
    snapshots = [
        skill.model_dump(
            mode="json",
            include={
                "id",
                "name",
                "instructions",
                "version",
                "content_type",
                "source_type",
                "output_schema",
            },
        )
        for skill in skills
    ]
    if sum(len(item["instructions"]) for item in snapshots) > 160000:
        raise APIError(422, "references_too_large", "关联内容过长，请减少提示词或 Skills")
    turn = AgentTurn(
        conversation_id=conversation.id,
        request_id=str(payload.request_id),
        sequence=sequence,
        content=payload.content,
        skill_snapshot=snapshots,
        config_snapshot=config,
    )
    db.add(turn)
    if sequence == 1:
        conversation.title = payload.content[:120]
    conversation.updated_at = utcnow()
    await db.commit()
    return turn


@router.post(
    "/conversations/{conversation_id}/messages/{turn_id}/cancel", response_model=AgentTurnOut
)
async def cancel_message(
    conversation_id: UUID, turn_id: UUID, db: DbSession, admin: CurrentAdmin
) -> AgentTurn:
    conversation, turn = await owned_turn(db, str(conversation_id), str(turn_id), admin.id)
    if turn.status in ACTIVE:
        turn.status = "cancelled"
        turn.claim_token = None
        turn.lease_expires_at = None
        turn.updated_at = conversation.updated_at = utcnow()
    await db.commit()
    return turn


@router.post(
    "/conversations/{conversation_id}/messages/{turn_id}/retry",
    response_model=AgentTurnOut,
    status_code=202,
)
async def retry_message(
    conversation_id: UUID, turn_id: UUID, db: DbSession, admin: CurrentAdmin
) -> AgentTurn:
    conversation, turn = await owned_turn(db, str(conversation_id), str(turn_id), admin.id)
    latest = await db.scalar(
        select(func.max(AgentTurn.sequence)).where(AgentTurn.conversation_id == conversation.id)
    )
    if turn.sequence != latest or turn.status not in ("failed", "cancelled"):
        raise APIError(409, "retry_not_allowed", "只能重试最后一条失败或取消的消息")
    turn.config_snapshot = await generation_config(db)
    turn.status, turn.error = "queued", None
    turn.claim_token = turn.lease_expires_at = None
    turn.updated_at = conversation.updated_at = utcnow()
    await db.commit()
    return turn


@router.post("/conversations/{conversation_id}/messages/{turn_id}/article")
async def save_article(
    conversation_id: UUID, turn_id: UUID, db: DbSession, admin: CurrentAdmin
) -> dict[str, str]:
    _, turn = await owned_turn(db, str(conversation_id), str(turn_id), admin.id)
    if turn.status != "succeeded" or not turn.article:
        raise APIError(409, "article_unavailable", "这条消息还没有可保存的文章")
    if turn.article_id is None:
        draft = AIDraft(
            **turn.article,
            article_source="ai",
            images=[],
            draft_metadata={
                "agent_conversation_id": turn.conversation_id,
                "agent_turn_id": turn.id,
            },
        )
        db.add(draft)
        await db.flush()
        turn.article_id = draft.id
    await db.commit()
    return {"article_id": str(turn.article_id)}
