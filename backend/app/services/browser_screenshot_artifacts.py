"""Short-lived binary job results on the browser host; Redis holds metadata only."""

import base64
import hashlib
import os
import re
import time
from pathlib import Path

MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024


def artifact_path(root: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise ValueError("Invalid screenshot job ID")
    return root / f"{job_id}.png"


def store_artifact(root: Path, job_id: str, result: dict) -> dict:
    encoded = result.get("png_base64")
    if not isinstance(encoded, str) or len(encoded) > ((MAX_SCREENSHOT_BYTES + 2) // 3) * 4:
        raise ValueError("X 截图内容过大或无效")
    contents = base64.b64decode(encoded, validate=True)
    if (
        len(contents) > MAX_SCREENSHOT_BYTES
        or not contents.startswith(b"\x89PNG\r\n\x1a\n")
        or hashlib.sha256(contents).hexdigest() != result.get("sha256")
    ):
        raise ValueError("X 截图内容校验失败")
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    target = artifact_path(root, job_id)
    temporary = target.with_suffix(".tmp")
    try:
        temporary.write_bytes(contents)
        temporary.chmod(0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        **{key: value for key, value in result.items() if key != "png_base64"},
        "screenshot_ready": True,
        "size_bytes": len(contents),
    }


def expire_artifacts(root: Path, ttl_seconds: int) -> None:
    cutoff = time.time() - ttl_seconds
    for path in root.glob("*"):
        if path.suffix not in {".png", ".tmp"}:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            continue
