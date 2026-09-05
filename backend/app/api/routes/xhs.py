from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbSession
from app.api.errors import APIError
from app.core.config import get_settings
from app.services.x_credentials import XCredentialUnavailableError
from app.services.xhs_credentials import (
    XiaohongshuCredentialValue,
    get_xhs_credentials,
    save_xhs_credentials,
)

router = APIRouter(prefix="/xhs", tags=["Xiaohongshu"])
CLI_HOME_ROOT = Path(os.getenv("XHS_CLI_HOME") or os.getenv("HOME", "/tmp/xsentinel-xhs"))
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


def _user_cli_home(admin_id: int) -> Path:
    return CLI_HOME_ROOT / "users" / str(admin_id)


def _write_upload(target: Path, contents: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(contents)
    target.chmod(0o640)


def _validated_image_path(image: str) -> Path | None:
    path = Path(image).resolve()
    if path.is_file() and UPLOAD_DIR in path.parents:
        return path
    return None


async def _run(admin_id: int, *args: str) -> tuple[int, str, str]:
    if shutil.which("xhs") is None:
        return 127, "", "xhs-cli 未安装，请在后端环境安装 xhs-cli"
    home = _user_cli_home(admin_id)
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    process = await asyncio.create_subprocess_exec(
        "xhs",
        *args,
        env={**os.environ, "HOME": str(home)},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return (
        process.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def _stored_credentials(db: AsyncSession, admin_id: int) -> XiaohongshuCredentialValue | None:
    try:
        return await get_xhs_credentials(db, get_settings(), admin_id=admin_id)
    except XCredentialUnavailableError as exc:
        raise APIError(503, "credential_encryption_unavailable", str(exc)) from None


async def _restore_cli_login(
    db: AsyncSession, admin_id: int
) -> tuple[XiaohongshuCredentialValue | None, int, str, str]:
    credentials = await _stored_credentials(db, admin_id)
    if credentials is None:
        return None, 0, "", ""
    code, out, err = await _run(
        admin_id,
        "login",
        "--cookie",
        f"a1={credentials.a1}; web_session={credentials.web_session}",
    )
    return credentials, code, out, err


@router.get("/status")
async def status(db: DbSession, admin: CurrentAdmin) -> dict:
    credentials, code, _, err = await _restore_cli_login(db, admin.id)
    if credentials is None:
        return {
            "saved": False,
            "connected": False,
            "installed": shutil.which("xhs") is not None,
        }
    if code:
        return {
            "saved": True,
            "connected": False,
            "installed": code != 127,
            "message": err.strip(),
        }
    code, out, err = await _run(admin.id, "whoami", "--json")
    if code:
        return {
            "saved": True,
            "connected": False,
            "installed": code != 127,
            "message": err.strip(),
        }
    try:
        profile = json.loads(out)
    except json.JSONDecodeError:
        profile = {"raw": out.strip()}
    return {
        "saved": True,
        "connected": True,
        "installed": True,
        "profile": profile,
    }


@router.post("/login")
async def login(payload: LoginPayload, db: DbSession, admin: CurrentAdmin) -> dict:
    code, out, err = await _run(
        admin.id,
        "login",
        "--cookie",
        f"a1={payload.a1}; web_session={payload.web_session}",
    )
    if code:
        raise HTTPException(400, detail=err.strip() or out.strip() or "小红书登录失败")
    try:
        await save_xhs_credentials(
            db,
            get_settings(),
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
async def post(payload: PostPayload, db: DbSession, admin: CurrentAdmin) -> dict:
    credentials, code, out, err = await _restore_cli_login(db, admin.id)
    if credentials is None:
        raise APIError(409, "xhs_credentials_not_configured", "请先保存小红书登录态")
    if code:
        raise HTTPException(502, detail=err.strip() or out.strip() or "恢复小红书登录态失败")

    args = ["post", payload.title, "--content", payload.content]
    for image in payload.images:
        path = await asyncio.to_thread(_validated_image_path, image)
        if path is None:
            raise HTTPException(400, detail="图片路径无效")
        args.extend(["--image", str(path)])
    code, out, err = await _run(admin.id, *args, "--json")
    if code:
        raise HTTPException(502, detail=err.strip() or out.strip() or "发布失败")
    try:
        result = json.loads(out)
    except json.JSONDecodeError:
        result = {"raw": out.strip()}
    return {"message": "笔记发布成功", "result": result}
