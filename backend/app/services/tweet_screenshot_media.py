"""Validate capture responses and atomically store PNGs on the receiving server."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import struct
import tempfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.core.time import as_utc

TWEET_SCREENSHOT_DIR = Path(
    os.getenv("TWEET_SCREENSHOT_DIR", "/var/lib/xsentinel/tweet-screenshots")
)
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class ValidatedScreenshot:
    png: bytes
    sha256: str
    width: int
    height: int
    canonical_url: str
    captured_at: datetime


def png_dimensions(png: bytes) -> tuple[int, int]:
    if not png.startswith(PNG_SIGNATURE) or len(png) > MAX_SCREENSHOT_BYTES:
        raise ValueError("Invalid screenshot PNG or image exceeds 8 MiB")
    offset, width, height = 8, 0, 0
    compressed = bytearray()
    ended = False
    while offset + 12 <= len(png):
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        kind = png[offset + 4 : offset + 8]
        end = offset + 8 + length
        if end + 4 > len(png):
            raise ValueError("Truncated screenshot PNG")
        chunk = png[offset + 8 : end]
        if zlib.crc32(kind + chunk) & 0xFFFFFFFF != struct.unpack(">I", png[end : end + 4])[0]:
            raise ValueError("Screenshot PNG checksum failed")
        if offset == 8:
            if kind != b"IHDR" or length != 13:
                raise ValueError("Screenshot PNG header is missing")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
            if (
                not (0 < width <= 16384 and 0 < height <= 24000)
                or width * height > 40_000_000
                or depth != 8
                or color not in (2, 6)
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise ValueError("Unsupported screenshot PNG dimensions or encoding")
            channels = 3 if color == 2 else 4
        elif kind == b"IHDR":
            raise ValueError("Duplicate screenshot PNG header")
        if kind == b"IDAT":
            compressed.extend(chunk)
        if kind == b"IEND":
            if length != 0 or end + 4 != len(png):
                raise ValueError("Invalid screenshot PNG ending")
            ended = True
            break
        offset = end + 4
    if not ended or not compressed:
        raise ValueError("Incomplete screenshot PNG")
    expected_bytes = height * (width * channels + 1)
    decoder = zlib.decompressobj()
    row_bytes = width * channels + 1
    decoded_bytes = 0
    pending = bytes(compressed)
    try:
        while pending:
            pixels = decoder.decompress(pending, 65536)
            pending = decoder.unconsumed_tail
            if decoded_bytes + len(pixels) > expected_bytes:
                raise ValueError("Screenshot PNG pixel length is invalid")
            first_filter = (-decoded_bytes) % row_bytes
            if any(value > 4 for value in pixels[first_filter::row_bytes]):
                raise ValueError("Screenshot PNG scanline filter is invalid")
            decoded_bytes += len(pixels)
            if decoder.eof:
                break
    except zlib.error as exc:
        raise ValueError("Screenshot PNG pixels are invalid") from exc
    if decoded_bytes != expected_bytes or not decoder.eof or decoder.unused_data or pending:
        raise ValueError("Screenshot PNG pixel length is invalid")
    return width, height


def validate_screenshot(data: dict, *, tweet_id: str, username: str) -> ValidatedScreenshot:
    if str(data.get("tweet_id")) != tweet_id:
        raise ValueError("Screenshot post ID does not match the request")
    if str(data.get("username", "")).lower() != username.lower():
        raise ValueError("Screenshot author does not match the request")
    canonical_url = str(data.get("canonical_url", ""))
    url = urlsplit(canonical_url)
    if (
        url.scheme != "https"
        or url.netloc != "x.com"
        or url.path.lower() != f"/{username}/status/{tweet_id}".lower()
        or url.query
        or url.fragment
    ):
        raise ValueError("Screenshot URL does not match the request")
    encoded = data.get("png_base64")
    if not isinstance(encoded, str) or len(encoded) > (MAX_SCREENSHOT_BYTES + 2) // 3 * 4:
        raise ValueError("Invalid screenshot content size")
    try:
        png = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid screenshot base64") from exc
    digest = hashlib.sha256(png).hexdigest()
    if digest != data.get("sha256"):
        raise ValueError("Screenshot SHA256 does not match")
    width, height = png_dimensions(png)
    if data.get("width") != width or data.get("height") != height:
        raise ValueError("Screenshot dimensions do not match")
    try:
        captured_at = as_utc(
            datetime.fromisoformat(str(data["captured_at"]).replace("Z", "+00:00"))
        )
    except (ValueError, KeyError) as exc:
        raise ValueError("Screenshot capture time is invalid") from exc
    return ValidatedScreenshot(png, digest, width, height, canonical_url, captured_at)


def screenshot_path(relative_name: str | None) -> Path | None:
    if not relative_name or not re.fullmatch(r"[0-9]{1,32}/[a-f0-9]{32}\.png", relative_name):
        return None
    path = (TWEET_SCREENSHOT_DIR / relative_name).resolve()
    if TWEET_SCREENSHOT_DIR.resolve() not in path.parents or not path.is_file():
        return None
    return path


def write_screenshot(tweet_id: str, token: str, png: bytes) -> str:
    relative_name = f"{tweet_id}/{token}.png"
    if not re.fullmatch(r"[0-9]{1,32}/[a-f0-9]{32}\.png", relative_name):
        raise ValueError("Invalid screenshot storage name")
    target = TWEET_SCREENSHOT_DIR / relative_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o750)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as file:
            temporary = file.name
            file.write(png)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return relative_name


def delete_screenshot(relative_name: str | None) -> None:
    path = screenshot_path(relative_name)
    if path is not None:
        path.unlink(missing_ok=True)
