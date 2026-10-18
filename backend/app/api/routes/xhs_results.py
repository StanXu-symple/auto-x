"""Internal worker callback. Administrator tokens cannot call this endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from app.api.deps import DbSession
from app.models.browser_job import BrowserJobResult
from app.services.xhs_publish_results import apply_xhs_job_result

router = APIRouter(prefix="/internal/xhs", include_in_schema=False)


async def require_xhs_result_sender(request: Request) -> dict:
    verifier = getattr(request.app.state, "xhs_result_verifier", None)
    if verifier is None:
        raise HTTPException(503, "Service callback authentication is unavailable")
    claims = await verifier(request)
    if claims.get("sub") != "xhs-worker":
        raise HTTPException(403, "Only xhs-worker may report publication results")
    return claims


@router.post("/jobs/{job_id}/completion")
async def complete_xhs_job(
    job_id: Annotated[str, Path(pattern=r"^[a-f0-9]{32}$")],
    db: DbSession,
    _: Annotated[dict, Depends(require_xhs_result_sender)],
) -> dict:
    # The callback carries only a job id. A forged success/failure body cannot
    # change the canonical state written by the worker before notification.
    result = await db.get(BrowserJobResult, ("xhs", job_id))
    if result is None:
        raise HTTPException(404, "XHS job result not found")
    if result.state not in ("succeeded", "failed", "uncertain"):
        raise HTTPException(409, "XHS job has not finished")
    applied = await apply_xhs_job_result(db, job_id)
    await db.commit()
    return {"accepted": True, "applied": applied}
