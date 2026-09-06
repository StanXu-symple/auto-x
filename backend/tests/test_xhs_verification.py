import os
from pathlib import Path

from app.services.xhs_verification import (
    clear_verification_image,
    cli_admin_id,
    read_verification_image,
    verification_image_path,
)


def test_verification_image_is_scoped_to_admin(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    first = verification_image_path(7)
    second = verification_image_path(8)
    first.parent.mkdir(parents=True)
    first.write_bytes(b"png")

    assert first != second
    assert read_verification_image(7) == (b"png", first.stat().st_mtime_ns)
    assert read_verification_image(8) is None

    clear_verification_image(7)
    assert not first.exists()


def test_cli_admin_id_comes_from_isolated_home(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/var/lib/xsentinel/xhs-home/users/42")
    assert cli_admin_id() == 42

    monkeypatch.setenv("HOME", os.devnull)
    assert cli_admin_id() is None


def test_expired_verification_image_is_removed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    image = verification_image_path(9)
    image.parent.mkdir(parents=True)
    image.write_bytes(b"expired")
    os.utime(image, (0, 0))

    assert read_verification_image(9) is None
    assert not image.exists()
