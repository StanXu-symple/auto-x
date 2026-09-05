from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from app.api.deps import CurrentAdmin, DbSession, RedisClient
from app.api.errors import APIError
from app.core.config import get_settings
from app.services.x_credentials import XCredentialUnavailableError, encrypt_token
from app.services.xhs_credentials import has_xhs_credentials, save_xhs_credentials
from app.services.xhs_jobs import (
    XHSJobFailedError,
    XHSJobTimeoutError,
    XHSWorkerUnavailableError,
    get_xhs_worker_status,
    submit_xhs_job,
)

router = APIRouter(prefix="/xhs", tags=["Xiaohongshu"])
UPLOAD_DIR = Path(os.getenv("XHS_UPLOAD_DIR", "/var/lib/xsentinel/xhs-uploads"))


class LoginPayload(BaseModel):
    a1: str = Field(min_length=3, max_length=4096)
    web_session: str = Field(min_length=3, max_length=4096)

    @field_validator("a1", "web_session")
    @classmethod
    def clean_cookie_value(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Cookie value cannot be empty")
        return value


class PostPayload(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=20000)
    images: list[str] = Field(min_length=1, max_length=18)


def _write_upload(target: Path, contents: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(contents)
    target.chmod(0o640)


def _validated_image_path(image: str) -> Path | None:
    path = Path(image).resolve()
    if path.is_file() and UPLOAD_DIR in path.parents:
        return path
    return None


async def _submit(redis: RedisClient, *, operation: str, admin_id: int, payload: dict) -> dict:
    try:
        return await submit_xhs_job(
            redis,
            operation=operation,
            admin_id=admin_id,
            payload=payload,
            timeout_seconds=get_settings().xhs_job_timeout_seconds,
        )
    except XHSWorkerUnavailableError as exc:
        raise APIError(503, "xhs_worker_unavailable", str(exc)) from None
    except XHSJobTimeoutError as exc:
        raise APIError(504, "xhs_job_timeout", str(exc)) from None
    except XHSJobFailedError as exc:
        raise APIError(502, "xhs_job_failed", str(exc)) from None


@router.get("/status")
async def status(db: DbSession, redis: RedisClient, admin: CurrentAdmin) -> dict:
    saved = await has_xhs_credentials(db, admin_id=admin.id)
    worker = await get_xhs_worker_status(redis)
    return {
        "saved": saved,
        "connected": saved and worker["status"] == "online" and bool(worker.get("installed")),
        "installed": bool(worker.get("installed")),
        "worker_status": worker["status"],
    }


@router.post("/login")
async def login(
    payload: LoginPayload, db: DbSession, redis: RedisClient, admin: CurrentAdmin
) -> dict:
    settings = get_settings()
    try:
        encrypted_payload = {
            "encrypted_a1": encrypt_token(payload.a1, settings),
            "encrypted_web_session": encrypt_token(payload.web_session, settings),
        }
        await _submit(redis, operation="login", admin_id=admin.id, payload=encrypted_payload)
        await save_xhs_credentials(
            db,
            settings,
            admin_id=admin.id,
            a1=payload.a1,
            web_session=payload.web_session,
        )
    except XCredentialUnavailableError as exc:
        raise APIError(503, "credential_encryption_unavailable", str(exc)) from None
    return {"message": "小红书登录态已加密保存"}


@router.post("/uploads")
async def upload(_: CurrentAdmin, files: list[UploadFile] = File(...)) -> dict:
    result = []
    for file in files:
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(400, detail="仅支持 JPG、PNG、WebP 图片")
        target = UPLOAD_DIR / f"{os.urandom(12).hex()}{suffix}"
        await asyncio.to_thread(_write_upload, target, await file.read())
        result.append({"path": str(target)})
    return {"files": result}


@router.post("/posts")
async def post(
    payload: PostPayload, db: DbSession, redis: RedisClient, admin: CurrentAdmin
) -> dict:
    if not await has_xhs_credentials(db, admin_id=admin.id):
        raise APIError(409, "xhs_credentials_not_configured", "请先保存小红书登录态")
    for image in payload.images:
        path = await asyncio.to_thread(_validated_image_path, image)
        if path is None:
            raise HTTPException(400, detail="图片路径无效")
    return await _submit(
        redis,
        operation="post",
        admin_id=admin.id,
        payload=payload.model_dump(),
    )
