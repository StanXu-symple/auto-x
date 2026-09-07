import asyncio
import threading
import time
from pathlib import Path

import pytest

from app.services import xhs_browser_pool as pool_module
from app.services.xhs_browser_pool import XiaohongshuBrowserPool


class FakeClient:
    def __init__(self, tracker: dict, profile_dir: Path) -> None:
        self.tracker = tracker
        self.profile_dir = profile_dir
        self._page = None

    def start(self) -> None:
        self._page = FakePage()
        self.tracker["starts"] += 1

    def publish_note(self, **kwargs):
        with self.tracker["lock"]:
            self.tracker["active"] += 1
            self.tracker["max_active"] = max(
                self.tracker["max_active"], self.tracker["active"]
            )
        try:
            time.sleep(self.tracker.get("delay", 0))
            self.tracker["publishes"].append(kwargs["title"])
            return {"success": True, "note_id": kwargs["title"]}
        finally:
            with self.tracker["lock"]:
                self.tracker["active"] -= 1

    def close(self) -> None:
        self.tracker["closes"] += 1
        self._page = None


class FakePage:
    def is_closed(self) -> bool:
        return False


def make_tracker(*, delay: float = 0) -> dict:
    return {
        "starts": 0,
        "closes": 0,
        "publishes": [],
        "active": 0,
        "max_active": 0,
        "delay": delay,
        "lock": threading.Lock(),
        "profiles": [],
    }


def install_fake_factory(monkeypatch, tracker: dict) -> None:
    def factory(
        _cookies: dict[str, str], profile_dir: Path, _cookie_version: int
    ) -> FakeClient:
        tracker["profiles"].append(profile_dir)
        return FakeClient(tracker, profile_dir)

    monkeypatch.setattr(pool_module, "_create_persistent_client", factory)


async def publish(pool: XiaohongshuBrowserPool, admin_id: int, version: int, title: str):
    return await pool.publish(
        admin_id=admin_id,
        cookie_version=version,
        cookie_dict={"a1": "a1", "web_session": "session"},
        title=title,
        content="content",
        image_paths=["/tmp/image.jpg"],
    )


async def test_pool_reuses_one_browser_for_same_admin(tmp_path, monkeypatch) -> None:
    tracker = make_tracker()
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=1, max_concurrency=1)

    first = await publish(pool, 7, 1, "first")
    second = await publish(pool, 7, 1, "second")

    assert first["note_id"] == "first"
    assert second["note_id"] == "second"
    assert tracker["starts"] == 1
    assert tracker["publishes"] == ["first", "second"]
    assert pool.size == 1

    await pool.close()
    assert tracker["closes"] == 1


async def test_pool_evicts_idle_lru_browser_at_capacity(tmp_path, monkeypatch) -> None:
    tracker = make_tracker()
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=1, max_concurrency=1)

    await publish(pool, 7, 1, "first")
    await publish(pool, 8, 1, "second")

    assert tracker["starts"] == 2
    assert tracker["closes"] == 1
    assert pool.size == 1
    assert tracker["profiles"] == [
        tmp_path / "users" / "7" / "browser-profile",
        tmp_path / "users" / "8" / "browser-profile",
    ]

    await pool.close()


async def test_pool_restarts_browser_when_cookie_version_changes(tmp_path, monkeypatch) -> None:
    tracker = make_tracker()
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=1, max_concurrency=1)

    await publish(pool, 7, 1, "old")
    await publish(pool, 7, 2, "new")

    assert tracker["starts"] == 2
    assert tracker["closes"] == 1

    await pool.close()


async def test_business_concurrency_is_independent_from_pool_size(
    tmp_path, monkeypatch
) -> None:
    tracker = make_tracker(delay=0.05)
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=2, max_concurrency=1)

    await asyncio.gather(
        publish(pool, 7, 1, "first"),
        publish(pool, 8, 1, "second"),
    )

    assert tracker["max_active"] == 1
    assert pool.size == 2

    await pool.close()


async def test_pool_allows_configured_parallel_business_operations(
    tmp_path, monkeypatch
) -> None:
    tracker = make_tracker(delay=0.05)
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=2, max_concurrency=2)

    await asyncio.gather(
        publish(pool, 7, 1, "first"),
        publish(pool, 8, 1, "second"),
    )

    assert tracker["max_active"] == 2

    await pool.close()


async def test_cancelled_publish_retires_browser_after_sync_work_finishes(
    tmp_path, monkeypatch
) -> None:
    tracker = make_tracker(delay=0.05)
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=1, max_concurrency=1)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(publish(pool, 7, 1, "slow"), timeout=0.01)

    assert pool.size == 0
    await pool.close()
    assert tracker["closes"] == 1
