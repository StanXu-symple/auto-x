from __future__ import annotations

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.models.ai import AIAgentNode, AIAgentWorkflow, AIAgentWorkflowStep
from app.schemas.agent_workflow import (
    NodeInput,
    NodeOut,
    NodeUpdate,
    WorkflowInput,
    WorkflowOut,
    WorkflowUpdate,
)
from app.schemas.common import MessageResponse, Page
from app.services.skill_client import get_skill_client

router = APIRouter(prefix="/ai", tags=["Agent Workflows"])


def _node_out(node: AIAgentNode) -> NodeOut:
    return NodeOut(
        id=node.id,
        name=node.name,
        description=node.description,
        node_type=node.node_type,
        skill_ids=list(node.skill_ids or []),
        is_active=node.is_active,
        version=node.version,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _workflow_out(workflow: AIAgentWorkflow) -> WorkflowOut:
    steps = sorted(workflow.steps or [], key=lambda row: row.position)
    nodes = [step.node for step in steps if step.node is not None]
    return WorkflowOut(
        id=workflow.id,
        name=workflow.name,
        description=workflow.description,
        node_ids=[step.node_id for step in steps],
        is_active=workflow.is_active,
        version=workflow.version,
        nodes=[_node_out(node) for node in nodes],
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
    )


async def _check_skills(skill_ids: list[int]) -> None:
    skills = await get_skill_client().resolve(skill_ids)
    if [skill.id for skill in skills] != skill_ids or any(not skill.is_active for skill in skills):
        raise APIError(422, "invalid_agent_skills", "节点关联的提示词或 Skill 不存在或未启用")


async def _check_nodes(db: DbSession, node_ids: list[int]) -> list[AIAgentNode]:
    nodes = list(
        await db.scalars(
            select(AIAgentNode)
            .where(AIAgentNode.id.in_(node_ids))
            .order_by(AIAgentNode.id)
            .with_for_update()
        )
    )
    by_id = {node.id: node for node in nodes}
    if len(nodes) != len(set(node_ids)) or any(
        not by_id[node_id].is_active for node_id in node_ids
    ):
        raise APIError(422, "invalid_agent_nodes", "流程包含不存在或已停用的 Agent 节点")
    return [by_id[node_id] for node_id in node_ids]


@router.get("/agent-nodes", response_model=Page[NodeOut])
async def list_agent_nodes(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    active: bool | None = None,
) -> Page[NodeOut]:
    conditions = [] if active is None else [AIAgentNode.is_active == active]
    total = int(await db.scalar(select(func.count(AIAgentNode.id)).where(*conditions)) or 0)
    nodes = list(
        await db.scalars(
            select(AIAgentNode)
            .where(*conditions)
            .order_by(AIAgentNode.updated_at.desc(), AIAgentNode.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(
        items=[_node_out(node) for node in nodes], total=total, page=page, page_size=page_size
    )


@router.post("/agent-nodes", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
async def create_agent_node(payload: NodeInput, db: DbSession, _: CurrentAdmin) -> NodeOut:
    await _check_skills(payload.skill_ids)
    node = AIAgentNode(**payload.model_dump(), version=1)
    db.add(node)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "agent_node_name_exists", "Agent 节点名称已存在") from None
    await db.refresh(node)
    return _node_out(node)


@router.get("/agent-nodes/{node_id}", response_model=NodeOut)
async def get_agent_node(node_id: int, db: DbSession, _: CurrentAdmin) -> NodeOut:
    node = await db.get(AIAgentNode, node_id)
    if node is None:
        raise APIError(404, "agent_node_not_found", "Agent 节点不存在")
    return _node_out(node)


@router.patch("/agent-nodes/{node_id}", response_model=NodeOut)
async def update_agent_node(
    node_id: int, payload: NodeUpdate, db: DbSession, _: CurrentAdmin
) -> NodeOut:
    node = await db.get(AIAgentNode, node_id, with_for_update=True)
    if node is None:
        raise APIError(404, "agent_node_not_found", "Agent 节点不存在")
    if node.version != payload.version:
        raise APIError(409, "agent_node_version_conflict", "Agent 节点已被其他操作修改")
    if payload.is_active or payload.skill_ids != node.skill_ids:
        await _check_skills(payload.skill_ids)
    for key, value in payload.model_dump(exclude={"version"}).items():
        setattr(node, key, value)
    node.version += 1
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "agent_node_name_exists", "Agent 节点名称已存在") from None
    await db.refresh(node)
    return _node_out(node)


@router.delete("/agent-nodes/{node_id}", response_model=MessageResponse)
async def disable_agent_node(node_id: int, db: DbSession, _: CurrentAdmin) -> MessageResponse:
    node = await db.get(AIAgentNode, node_id, with_for_update=True)
    if node is None:
        raise APIError(404, "agent_node_not_found", "Agent 节点不存在")
    node.is_active = False
    node.version += 1
    await db.commit()
    return MessageResponse(message="Agent 节点已停用")


@router.get("/agent-workflows", response_model=Page[WorkflowOut])
async def list_agent_workflows(
    db: DbSession,
    _: CurrentAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    active: bool | None = None,
) -> Page[WorkflowOut]:
    conditions = [] if active is None else [AIAgentWorkflow.is_active == active]
    total = int(await db.scalar(select(func.count(AIAgentWorkflow.id)).where(*conditions)) or 0)
    workflows = list(
        await db.scalars(
            select(AIAgentWorkflow)
            .options(selectinload(AIAgentWorkflow.steps).joinedload(AIAgentWorkflowStep.node))
            .where(*conditions)
            .order_by(AIAgentWorkflow.updated_at.desc(), AIAgentWorkflow.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return Page(
        items=[_workflow_out(workflow) for workflow in workflows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/agent-workflows/{workflow_id}", response_model=WorkflowOut)
async def get_agent_workflow(workflow_id: int, db: DbSession, _: CurrentAdmin) -> WorkflowOut:
    workflow = await db.scalar(
        select(AIAgentWorkflow)
        .options(selectinload(AIAgentWorkflow.steps).joinedload(AIAgentWorkflowStep.node))
        .where(AIAgentWorkflow.id == workflow_id)
    )
    if workflow is None:
        raise APIError(404, "agent_workflow_not_found", "Agent 流程不存在")
    return _workflow_out(workflow)


@router.post("/agent-workflows", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
async def create_agent_workflow(
    payload: WorkflowInput, db: DbSession, _: CurrentAdmin
) -> WorkflowOut:
    await _check_nodes(db, payload.node_ids)
    workflow = AIAgentWorkflow(
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
        version=1,
    )
    workflow.steps = [
        AIAgentWorkflowStep(node_id=node_id, position=position)
        for position, node_id in enumerate(payload.node_ids, 1)
    ]
    db.add(workflow)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "agent_workflow_name_exists", "Agent 流程名称已存在") from None
    return await get_agent_workflow(workflow.id, db, None)  # type: ignore[arg-type]


@router.put("/agent-workflows/{workflow_id}", response_model=WorkflowOut)
async def update_agent_workflow(
    workflow_id: int, payload: WorkflowUpdate, db: DbSession, _: CurrentAdmin
) -> WorkflowOut:
    workflow = await db.scalar(
        select(AIAgentWorkflow)
        .options(selectinload(AIAgentWorkflow.steps))
        .where(AIAgentWorkflow.id == workflow_id)
        .with_for_update()
    )
    if workflow is None:
        raise APIError(404, "agent_workflow_not_found", "Agent 流程不存在")
    if workflow.version != payload.version:
        raise APIError(409, "agent_workflow_version_conflict", "Agent 流程已被其他操作修改")
    nodes = await _check_nodes(db, payload.node_ids)
    workflow.name = payload.name
    workflow.description = payload.description
    workflow.is_active = payload.is_active
    workflow.version += 1
    workflow.steps.clear()
    await db.flush()
    workflow.steps = [
        AIAgentWorkflowStep(node_id=node.id, position=position)
        for position, node in enumerate(nodes, 1)
    ]
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise APIError(409, "agent_workflow_name_exists", "Agent 流程名称已存在") from None
    return await get_agent_workflow(workflow.id, db, None)  # type: ignore[arg-type]


@router.delete("/agent-workflows/{workflow_id}", response_model=MessageResponse)
async def disable_agent_workflow(
    workflow_id: int, db: DbSession, _: CurrentAdmin
) -> MessageResponse:
    workflow = await db.get(AIAgentWorkflow, workflow_id, with_for_update=True)
    if workflow is None:
        raise APIError(404, "agent_workflow_not_found", "Agent 流程不存在")
    workflow.is_active = False
    workflow.version += 1
    await db.commit()
    return MessageResponse(message="Agent 流程已停用")
