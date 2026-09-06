from __future__ import annotations

import os
from pathlib import Path

ARTICLE_UPLOAD_DIR = Path(os.getenv("ARTICLE_UPLOAD_DIR", "/var/lib/xsentinel/article-uploads"))
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_ARTICLE_IMAGE_BYTES = 10 * 1024 * 1024


def article_image_path(image: str, *, admin_id: int | None = None) -> Path | None:
    relative = Path(image)
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) != 2:
        return None
    if admin_id is not None and relative.parts[0] != str(admin_id):
        return None
    path = (ARTICLE_UPLOAD_DIR / relative).resolve()
    root = ARTICLE_UPLOAD_DIR.resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def article_delivery_media_path(value: str) -> Path | None:
    path = Path(value).resolve()
    root = ARTICLE_UPLOAD_DIR.resolve()
    if root in path.parents and path.is_file():
        return path
    return None


def write_article_image(relative_name: str, contents: bytes) -> Path:
    target = ARTICLE_UPLOAD_DIR / relative_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o750)
    target.write_bytes(contents)
    target.chmod(0o640)
    return target


def delete_article_image(image: str) -> None:
    path = article_image_path(image)
    if path is None:
        return
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


def clear_article_images(images: list[str]) -> None:
    for image in images:
        delete_article_image(image)
