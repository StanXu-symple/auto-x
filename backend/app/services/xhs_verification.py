from __future__ import annotations

import os
import time
from pathlib import Path

VERIFICATION_MAX_AGE_SECONDS = 180


def verification_root() -> Path:
    upload_dir = Path(
        os.getenv("XHS_UPLOAD_DIR", "/var/lib/xsentinel/xhs-uploads")
    ).resolve()
    return upload_dir / ".verification"


def verification_image_path(admin_id: int) -> Path:
    return verification_root() / f"{admin_id}.png"


def cli_admin_id() -> int | None:
    home = Path(os.getenv("HOME", "")).resolve()
    if home.parent.name != "users" or not home.name.isdigit():
        return None
    return int(home.name)


def clear_verification_image(admin_id: int) -> None:
    try:
        verification_image_path(admin_id).unlink(missing_ok=True)
    except OSError:
        pass


def read_verification_image(admin_id: int) -> tuple[bytes, int] | None:
    path = verification_image_path(admin_id)
    try:
        stat = path.stat()
        if not path.is_file() or time.time() - stat.st_mtime > VERIFICATION_MAX_AGE_SECONDS:
            path.unlink(missing_ok=True)
            return None
        return path.read_bytes(), stat.st_mtime_ns
    except OSError:
        return None

