from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai import AIDraft

ARTICLE_UPLOAD_DIR = Path(os.getenv("ARTICLE_UPLOAD_DIR", "/var/lib/xsentinel/article-uploads"))
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_ARTICLE_IMAGE_BYTES = 10 * 1024 * 1024
SOURCE_SCREENSHOT_IMAGES_KEY = "source_screenshot_images"
INCLUDE_SOURCE_SCREENSHOT_KEY = "include_source_screenshot"


def _screenshot_copy_names(article_id: int, metadata: dict | None) -> list[str]:
    candidates = (metadata or {}).get(SOURCE_SCREENSHOT_IMAGES_KEY, [])
    if not isinstance(candidates, list):
        return []
    return [
        image
        for image in candidates
        if isinstance(image, str)
        and re.fullmatch(rf"[0-9]+/source-{article_id}-[a-f0-9]{{64}}\.png", image)
    ]


def article_screenshot_copies(article: AIDraft) -> list[str]:
    return _screenshot_copy_names(article.id, article.draft_metadata)


def article_includes_source_screenshot(article: AIDraft) -> bool:
    # Existing articles have no preference and continue to include the source image.
    return (article.draft_metadata or {}).get(INCLUDE_SOURCE_SCREENSHOT_KEY) is not False


def preserve_article_media_metadata(
    article: AIDraft | None, metadata: dict | None
) -> dict | None:
    """Only the article editor may change media preferences and tracked copies."""
    result = dict(metadata) if metadata is not None else None
    if result is not None:
        result.pop(SOURCE_SCREENSHOT_IMAGES_KEY, None)
        result.pop(INCLUDE_SOURCE_SCREENSHOT_KEY, None)
    if article is not None:
        copies = article_screenshot_copies(article)
        if copies:
            result = {**(result or {}), SOURCE_SCREENSHOT_IMAGES_KEY: copies}
        preference = (article.draft_metadata or {}).get(INCLUDE_SOURCE_SCREENSHOT_KEY)
        if isinstance(preference, bool):
            result = {**(result or {}), INCLUDE_SOURCE_SCREENSHOT_KEY: preference}
    return result


async def clear_unreferenced_article_images(db: AsyncSession, candidates: list[str]) -> None:
    if not candidates:
        return
    referenced: set[str] = set()
    rows = await db.execute(select(AIDraft.id, AIDraft.images, AIDraft.draft_metadata))
    for article_id, images, metadata in rows:
        referenced.update(images or [])
        referenced.update(_screenshot_copy_names(article_id, metadata))
    unused = [image for image in candidates if image not in referenced]
    await asyncio.to_thread(clear_article_images, unused)


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


def snapshot_article_screenshot(
    source: Path, *, article_id: int, admin_id: int
) -> tuple[str, Path]:
    """Keep a stable publish copy in the media volume shared with publishing workers."""
    contents = source.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    image = f"{admin_id}/source-{article_id}-{digest}.png"
    target = ARTICLE_UPLOAD_DIR / image
    if article_image_path(image, admin_id=admin_id) is not None:
        return image, target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o750)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(contents)
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(0o640)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return image, target.resolve()


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
