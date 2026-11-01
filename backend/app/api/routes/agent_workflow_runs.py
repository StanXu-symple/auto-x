import json
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.api.routes.agent_workflows import _check_nodes
from app.db.base import utcnow
from app.models.agent_workflow import AgentWorkflowRun, AgentWorkflowStepRun
from app.models.ai import AIAgentWorkflow, AIDraft
from app.schemas.agent_workflow import RunDetail, RunInput, RunOut, StepOut
from app.schemas.common import Page
from app.services.agent_workflow import (
    MAX_CONTEXT,
    execution_config,
    freeze_workflow,
    request_hash,
    source_snapshot,
)

router = APIRouter(prefix="/ai/agent-workflow-runs", tags=["Agent Workflow Runs"])


async def owned_run(db, run_id, admin_id, *, lock=False):
    query = select(AgentWorkflowRun).where(
        AgentWorkflowRun.id == str(run_id), AgentWorkflowRun.admin_id == admin_id
    )
    if lock:
        query = query.with_for_update()
    run = await db.scalar(query)
    if not run:
        raise APIError(404, "workflow_run_not_found", "流程运行记录不存在")
    return run


async def step_rows(db, run_id):
    return list(
        await db.scalars(
            select(AgentWorkflowStepRun)
            .where(AgentWorkflowStepRun.run_id == run_id)
            .order_by(AgentWorkflowStepRun.position)
        )
    )


@router.post("", response_model=RunOut, status_code=202)
async def create_run(payload: RunInput, db: DbSession, admin: CurrentAdmin):
    digest = request_hash(payload)

    async def existing():
        row = await db.scalar(
            select(AgentWorkflowRun).where(
                AgentWorkflowRun.admin_id == admin.id,
                AgentWorkflowRun.request_id == str(payload.request_id),
            )
        )
        if row and row.request_hash != digest:
            raise APIError(409, "request_conflict", "同一请求编号不能用于不同流程或输入")
        return row

    if row := await existing():
        return row
    config = await execution_config(db)
    source = await source_snapshot(db, payload)
    if len(json.dumps(source, ensure_ascii=False)) + len(payload.input_text) > MAX_CONTEXT:
        raise APIError(422, "workflow_input_too_large", "来源内容过长")
    workflow = await db.scalar(
        select(AIAgentWorkflow)
        .where(AIAgentWorkflow.id == payload.workflow_id)
        .options(selectinload(AIAgentWorkflow.steps))
        .with_for_update()
    )
    if not workflow or not workflow.is_active:
        raise APIError(422, "workflow_unavailable", "流程不存在或已停用")
    if workflow.version != payload.workflow_version:
        raise APIError(409, "workflow_changed", "流程已更新，请重新选择后运行")
    ids = [step.node_id for step in sorted(workflow.steps, key=lambda step: step.position)]
    nodes = await _check_nodes(db, ids)
    if not nodes:
        raise APIError(422, "workflow_empty", "流程至少需要一个节点")
    snapshot = await freeze_workflow(workflow, nodes)
    run = AgentWorkflowRun(
        admin_id=admin.id,
        request_id=str(payload.request_id),
        request_hash=digest,
        workflow_id=workflow.id,
        workflow_name=workflow.name,
        workflow_version=workflow.version,
        input_text=payload.input_text,
        source_snapshot=source,
        workflow_snapshot=snapshot,
        config_snapshot=config,
    )
    try:
        db.add(run)
        await db.flush()
        for step in snapshot["steps"]:
            db.add(
                AgentWorkflowStepRun(
                    run_id=run.id,
                    position=step["position"],
                    node_name=step["name"],
                    node_type=step["node_type"],
                )
            )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if row := await existing():
            return row
        raise
    return run


@router.get("", response_model=Page[RunOut])
async def list_runs(
    db: DbSession,
    admin: CurrentAdmin,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    workflow_id: int | None = Query(None, ge=1),
    article_id: int | None = Query(None, ge=1),
):
    conditions = [AgentWorkflowRun.admin_id == admin.id]
    if workflow_id:
        conditions.append(AgentWorkflowRun.workflow_id == workflow_id)
    if article_id:
        conditions.append(
            or_(
                AgentWorkflowRun.article_id == article_id,
                AgentWorkflowRun.source_snapshot["article_id"].as_integer() == article_id,
            )
        )
    total = await db.scalar(select(func.count()).select_from(AgentWorkflowRun).where(*conditions))
    rows = await db.scalars(
        select(AgentWorkflowRun)
        .where(*conditions)
        .order_by(AgentWorkflowRun.created_at.desc(), AgentWorkflowRun.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page(items=list(rows), total=total or 0, page=page, page_size=page_size)


@router.get("/{run_id}", response_model=RunDetail)
async def detail(run_id: UUID, db: DbSession, admin: CurrentAdmin):
    run = await owned_run(db, run_id, admin.id)
    source = run.source_snapshot or {}
    changed = False
    if source.get("article_id"):
        article = await db.get(AIDraft, source["article_id"])
        changed = article is None or article.revision != source["revision"]
    return RunDetail(
        **RunOut.model_validate(run).model_dump(),
        input_text=run.input_text,
        source_snapshot=run.source_snapshot,
        workflow_snapshot=run.workflow_snapshot,
        config_snapshot=run.config_snapshot,
        steps=[StepOut.model_validate(s) for s in await step_rows(db, run.id)],
        source_changed=changed,
    )


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel(run_id: UUID, db: DbSession, admin: CurrentAdmin):
    run = await owned_run(db, run_id, admin.id, lock=True)
    if run.status in ("queued", "running", "retry_wait"):
        run.status = "cancelled"
        run.claim_token = None
        run.lease_expires_at = None
        run.completed_at = run.updated_at = utcnow()
        for step in await step_rows(db, run.id):
            if step.status != "succeeded":
                step.status = "cancelled"
        await db.commit()
    return run


@router.post("/{run_id}/retry", response_model=RunOut)
async def retry(run_id: UUID, db: DbSession, admin: CurrentAdmin):
    run = await owned_run(db, run_id, admin.id, lock=True)
    if run.status != "failed":
        raise APIError(409, "workflow_not_failed", "只能重试执行失败的流程")
    run.status, run.error = "queued", None
    run.completed_at = None
    run.next_attempt_at = run.updated_at = utcnow()
    for step in await step_rows(db, run.id):
        if step.status != "succeeded":
            step.status, step.attempts, step.error = "pending", 0, None
    await db.commit()
    return run


@router.post("/{run_id}/article")
async def save_article(run_id: UUID, db: DbSession, admin: CurrentAdmin):
    run = await owned_run(db, run_id, admin.id, lock=True)
    if run.article_id:
        return {"article_id": run.article_id}
    if run.status != "succeeded":
        raise APIError(409, "workflow_incomplete", "流程完成后才能保存文章")
    article_step = next(
        (s for s in reversed(await step_rows(db, run.id)) if s.node_type == "article" and s.output),
        None,
    )
    if article_step is None:
        raise APIError(409, "workflow_no_article", "此流程没有文章节点输出")
    # Save a new draft; a review or later edit must never overwrite an existing article.
    output = article_step.output or {}
    article = AIDraft(
        article_source="ai",
        title=output.get("title", "").strip(),
        content=output.get("content", "").strip(),
        excerpt=output.get("excerpt"),
        draft_metadata={
            "agent_workflow_run_id": run.id,
            "agent_workflow_name": run.workflow_name,
            "agent_workflow_version": run.workflow_version,
            "agent_workflow_article_output": output,
        },
    )
    db.add(article)
    await db.flush()
    run.article_id = article.id
    await db.commit()
    return {"article_id": article.id}
