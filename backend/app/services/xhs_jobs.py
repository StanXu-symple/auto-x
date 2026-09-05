from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

XHS_JOB_QUEUE = "xsentinel:xhs:jobs"
XHS_WORKER_HEARTBEAT = "xsentinel:xhs-worker:heartbeat"
XHS_RESPONSE_PREFIX = "xsentinel:xhs:responses:"
BROWSER_CLOSED_ERROR = "Target page, context or browser has been closed"


class XHSWorkerUnavailableError(RuntimeError):
    pass


class XHSJobTimeoutError(RuntimeError):
    pass


class XHSJobFailedError(RuntimeError):
    pass


def xhs_response_key(job_id: str) -> str:
    return f"{XHS_RESPONSE_PREFIX}{job_id}"


def publish_error(out: str, err: str) -> str:
    detail = err.strip() or out.strip() or "发布失败"
    if BROWSER_CLOSED_ERROR in out or BROWSER_CLOSED_ERROR in err:
        browser_detail = err.strip() if BROWSER_CLOSED_ERROR in err else out.strip()
        return (
            "小红书发布浏览器意外退出，请检查 xhs-worker 的 OOM "
            "和浏览器日志。原始错误：" + browser_detail
        )
    return detail


async def get_xhs_worker_status(redis: Redis) -> dict[str, Any]:
    raw = await redis.get(XHS_WORKER_HEARTBEAT)
    if not raw:
        return {"status": "offline", "installed": False}
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {"status": "unknown", "installed": False, "error": "Invalid worker heartbeat"}
    payload["status"] = "online"
    payload["ttl_seconds"] = await redis.ttl(XHS_WORKER_HEARTBEAT)
    return payload


async def submit_xhs_job(
    redis: Redis,
    *,
    operation: str,
    admin_id: int,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    worker = await get_xhs_worker_status(redis)
    if worker["status"] != "online" or not worker.get("installed"):
        raise XHSWorkerUnavailableError(
            "小红书发布 Worker 未就绪，请检查 xhs-worker 容器"
        )
    worker_id = str(worker.get("worker_id") or "")

    job_id = uuid.uuid4().hex
    response_key = xhs_response_key(job_id)
    job = {
        "job_id": job_id,
        "operation": operation,
        "admin_id": admin_id,
        "payload": payload,
        "requested_at": datetime.now(UTC).isoformat(),
    }
    await redis.rpush(XHS_JOB_QUEUE, json.dumps(job, ensure_ascii=False))

    deadline = asyncio.get_running_loop().time() + timeout_seconds
    next_worker_check = asyncio.get_running_loop().time() + 1
    while True:
        raw = await redis.get(response_key)
        if raw:
            await redis.delete(response_key)
            result = json.loads(raw)
            if not result.get("ok"):
                raise XHSJobFailedError(str(result.get("error") or "小红书任务执行失败"))
            data = result.get("data")
            return data if isinstance(data, dict) else {}
        now = asyncio.get_running_loop().time()
        if now >= next_worker_check:
            current_worker = await get_xhs_worker_status(redis)
            current_worker_id = str(current_worker.get("worker_id") or "")
            if current_worker["status"] != "online" or current_worker_id != worker_id:
                raise XHSJobFailedError(
                    "小红书发布 Worker 在任务执行期间退出或重启，请检查容器 OOM 和日志"
                )
            next_worker_check = now + 1
        if now >= deadline:
            raise XHSJobTimeoutError(f"小红书任务等待超过 {int(timeout_seconds)} 秒")
        await asyncio.sleep(0.25)
