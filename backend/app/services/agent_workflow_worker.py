"""Serial workflow execution with durable checkpoints and database-fenced leases."""

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.core.time import as_utc
from app.db.base import utcnow
from app.models.agent_workflow import AgentWorkflowRun, AgentWorkflowStepRun
from app.models.ai import AISetting
from app.services.agent_workflow import WorkflowExecutionError, execute_node
from app.services.ai_data_source import AIDataSourceUnavailableError, get_ai_data_source
from app.services.ai_provider import AIProviderError

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90
MAX_ATTEMPTS = 3


class WorkflowLeaseLost(Exception):
    pass


class AgentWorkflowWorker:
    def __init__(self, settings, redis, provider, session_factory, semaphore, stop_event):
        self.settings, self.redis, self.provider = settings, redis, provider
        self.session_factory, self.semaphore, self.stop_event = (
            session_factory,
            semaphore,
            stop_event,
        )
        self.active_tasks = 0

    async def run(self):
        while not self.stop_event.is_set():
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Agent workflow scan failed", extra={"error_type": type(exc).__name__})
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), self.settings.ai_worker_scan_interval_seconds
                )
            except TimeoutError:
                pass

    async def run_once(self):
        async with self.session_factory() as db, db.begin():
            await db.execute(
                update(AgentWorkflowRun)
                .where(
                    AgentWorkflowRun.status == "running",
                    AgentWorkflowRun.lease_expires_at <= utcnow(),
                )
                .values(
                    status="queued",
                    claim_token=None,
                    lease_expires_at=None,
                    next_attempt_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            setting = await db.get(AISetting, 1)
            if not setting or not setting.enabled:
                return
            ids = list(
                await db.scalars(
                    select(AgentWorkflowRun.id)
                    .where(
                        AgentWorkflowRun.status.in_(("queued", "retry_wait")),
                        AgentWorkflowRun.next_attempt_at <= utcnow(),
                    )
                    .order_by(AgentWorkflowRun.created_at)
                    .limit(self.settings.ai_worker_max_concurrency)
                )
            )
        tasks = [asyncio.create_task(self.process(run_id)) for run_id in ids]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(
                        "Agent workflow task failed", extra={"error_type": type(result).__name__}
                    )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def current(self, db, run_id, token):
        run = await db.scalar(
            select(AgentWorkflowRun).where(AgentWorkflowRun.id == run_id).with_for_update()
        )
        if (
            not run
            or run.status != "running"
            or run.claim_token != token
            or not run.lease_expires_at
            or as_utc(run.lease_expires_at) <= utcnow()
        ):
            raise WorkflowLeaseLost()
        return run

    async def renew(self, run_id, token, lost):
        try:
            while True:
                await asyncio.sleep(5)
                async with self.session_factory() as db, db.begin():
                    result = await db.execute(
                        update(AgentWorkflowRun)
                        .where(
                            AgentWorkflowRun.id == run_id,
                            AgentWorkflowRun.claim_token == token,
                            AgentWorkflowRun.status == "running",
                            AgentWorkflowRun.lease_expires_at > utcnow(),
                        )
                        .values(lease_expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS))
                    )
                    if result.rowcount != 1:
                        lost.set()
                        return
        except asyncio.CancelledError:
            raise
        except Exception:
            lost.set()

    async def process(self, run_id):
        async with self.semaphore:
            if self.stop_event.is_set():
                return
            token = str(uuid4())
            async with self.session_factory() as db, db.begin():
                claimed = await db.execute(
                    update(AgentWorkflowRun)
                    .where(
                        AgentWorkflowRun.id == run_id,
                        AgentWorkflowRun.status.in_(("queued", "retry_wait")),
                        AgentWorkflowRun.next_attempt_at <= utcnow(),
                    )
                    .values(
                        status="running",
                        claim_token=token,
                        error=None,
                        updated_at=utcnow(),
                        lease_expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS),
                    )
                )
                if claimed.rowcount != 1:
                    return
            self.active_tasks += 1
            lost = asyncio.Event()
            renew = asyncio.create_task(self.renew(run_id, token, lost))
            try:
                await self.execute(run_id, token, lost)
            except WorkflowLeaseLost:
                pass
            except asyncio.CancelledError:
                await self.fail(
                    run_id, token, "Worker 重启，等待继续", retryable=True, shutdown=True
                )
                raise
            except (AIDataSourceUnavailableError, AIProviderError, TimeoutError) as exc:
                retryable = isinstance(exc, TimeoutError) or (
                    isinstance(exc, AIProviderError) and exc.retryable
                )
                # Upstream exception text can contain a key or prompt. Never persist it.
                error = (
                    "模型请求超时"
                    if isinstance(exc, TimeoutError)
                    else "模型或数据源不可用，请检查配置"
                )
                if isinstance(exc, AIProviderError) and exc.status_code:
                    error = f"模型请求失败（HTTP {exc.status_code}）"
                if isinstance(exc, WorkflowExecutionError):
                    error = str(exc)
                await self.fail(run_id, token, error, retryable=retryable)
            except Exception as exc:
                logger.error(
                    "Agent workflow execution failed",
                    extra={"workflow_run_id": run_id, "error_type": type(exc).__name__},
                )
                await self.fail(run_id, token, "节点执行失败，请重试", retryable=True)
            finally:
                renew.cancel()
                await asyncio.gather(renew, return_exceptions=True)
                self.active_tasks -= 1

    async def execute(self, run_id, token, lost):
        while True:
            async with self.session_factory() as db, db.begin():
                run = await self.current(db, run_id, token)
                rows = list(
                    await db.scalars(
                        select(AgentWorkflowStepRun)
                        .where(AgentWorkflowStepRun.run_id == run_id)
                        .order_by(AgentWorkflowStepRun.position)
                    )
                )
                step = next((s for s in rows if s.status != "succeeded"), None)
                if step is None:
                    run.status = "succeeded"
                    run.completed_at = run.updated_at = utcnow()
                    run.claim_token = run.lease_expires_at = None
                    return
                if step.attempts >= MAX_ATTEMPTS:
                    step.status, step.error = "failed", "节点尝试次数已用完，请手动重试"
                    run.status, run.error = "failed", step.error
                    run.claim_token = run.lease_expires_at = None
                    run.completed_at = run.updated_at = utcnow()
                    return
                step.status, step.error = "running", None
                step.attempts += 1
                step.started_at, step.completed_at = utcnow(), None
                run.current_position = step.position
                run.updated_at = utcnow()
                node = run.workflow_snapshot["steps"][step.position - 1]
                history = [
                    {
                        "position": s.position,
                        "node": s.node_name,
                        "type": s.node_type,
                        "output": s.output,
                    }
                    for s in rows
                    if s.position < step.position
                ]
                position = step.position
            async with self.session_factory() as db:
                setting = await db.get(AISetting, 1)
                if not setting or not setting.enabled:
                    raise AIDataSourceUnavailableError("AI disabled")
                runtime = await get_ai_data_source(db, self.redis, self.settings)
            model_call = asyncio.create_task(
                execute_node(self.provider, runtime, run, node, history)
            )
            lost_task = asyncio.create_task(lost.wait())
            try:
                await asyncio.wait({model_call, lost_task}, return_when=asyncio.FIRST_COMPLETED)
                if lost.is_set():
                    raise WorkflowLeaseLost()
                output, usage = await model_call
            finally:
                for task in (model_call, lost_task):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(model_call, lost_task, return_exceptions=True)
            async with self.session_factory() as db, db.begin():
                await self.current(db, run_id, token)
                step = await db.get(AgentWorkflowStepRun, (run_id, position))
                step.status, step.output, step.usage = "succeeded", output, usage
                step.completed_at = utcnow()
            logger.info(
                "Agent workflow node completed",
                extra={
                    "workflow_run_id": run_id,
                    "node_position": position,
                    "node_type": node["node_type"],
                },
            )

    async def fail(self, run_id, token, error, *, retryable, shutdown=False):
        async with self.session_factory() as db, db.begin():
            try:
                run = await self.current(db, run_id, token)
            except WorkflowLeaseLost:
                return
            step = await db.get(AgentWorkflowStepRun, (run_id, run.current_position))
            again = retryable and (step is None or step.attempts < MAX_ATTEMPTS)
            run.status, run.error = ("retry_wait" if again else "failed"), error
            run.claim_token = run.lease_expires_at = None
            run.updated_at = utcnow()
            run.next_attempt_at = utcnow() + timedelta(seconds=0 if shutdown else 15)
            run.completed_at = None if again else utcnow()
            if step and step.status != "succeeded":
                step.status, step.error = ("pending" if again else "failed"), error
                step.completed_at = None if again else utcnow()
