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
        self._browser = object()
        self.tracker.setdefault("threads", []).append(threading.get_ident())
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


async def test_capture_reuses_xhs_context_on_same_thread_without_touching_publish_page(
    tmp_path, monkeypatch
) -> None:
    tracker = make_tracker()
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=1, max_concurrency=1)
    await publish(pool, 7, 1, "first")
    lease = pool._leases[7]
    original_page = lease.client._page

    def capture(context, **kwargs):
        assert context is lease.client._browser
        assert threading.get_ident() == tracker["threads"][0]
        return {"tweet_id": kwargs["tweet_id"]}

    monkeypatch.setattr(pool_module, "capture_tweet", capture)
    assert await pool.capture_tweet(tweet_id="123", username="user", expected_text="hi") == {
        "tweet_id": "123"
    }
    assert lease.client._page is original_page
    await publish(pool, 7, 1, "second")
    assert tracker["starts"] == 1
    assert pool.size == 1
    await pool.close()


async def test_public_capture_has_one_bounded_persistent_session_without_cookies(
    tmp_path, monkeypatch
) -> None:
    tracker = make_tracker(delay=0.02)

    def public_factory(profile):
        tracker["profiles"].append(profile)
        return FakeClient(tracker, profile)

    def capture(_context, **kwargs):
        time.sleep(tracker["delay"])
        return {"tweet_id": kwargs["tweet_id"]}

    monkeypatch.setattr(pool_module, "_create_public_client", public_factory)
    monkeypatch.setattr(pool_module, "capture_tweet", capture)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=2, max_concurrency=2)
    await asyncio.gather(*(
        pool.capture_tweet(tweet_id=str(i), username="user", expected_text="hi")
        for i in [123, 456]
    ))
    assert tracker["starts"] == 1
    assert tracker["profiles"] == [tmp_path / "public" / "browser-profile"]
    assert pool.size == 1
    await pool.close()


async def test_capture_and_publish_share_business_capacity(tmp_path, monkeypatch) -> None:
    tracker = make_tracker(delay=0.03)
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path, max_browsers=2, max_concurrency=1)
    await publish(pool, 7, 1, "first")

    def capture(_context, **_kwargs):
        with tracker["lock"]:
            tracker["active"] += 1
            tracker["max_active"] = max(tracker["max_active"], tracker["active"])
        time.sleep(tracker["delay"])
        with tracker["lock"]:
            tracker["active"] -= 1
        return {}

    monkeypatch.setattr(pool_module, "capture_tweet", capture)
    await asyncio.gather(
        pool.capture_tweet(tweet_id="123", username="user", expected_text="hi"),
        publish(pool, 8, 1, "second"),
    )
    assert tracker["max_active"] == 1
    await pool.close()


async def test_capture_identity_failure_preserves_xhs_browser(tmp_path, monkeypatch) -> None:
    tracker = make_tracker()
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path)
    await publish(pool, 7, 1, "first")

    def fail(_context, **_kwargs):
        raise pool_module.TweetCaptureError("Post unavailable")

    monkeypatch.setattr(pool_module, "capture_tweet", fail)
    with pytest.raises(pool_module.TweetCaptureError):
        await pool.capture_tweet(tweet_id="123", username="user", expected_text="hi")
    assert pool.size == 1
    assert pool.busy_count == 0
    assert tracker["closes"] == 0
    await pool.close()


async def test_cancelled_capture_waits_for_sync_thread_and_retires_session(
    tmp_path, monkeypatch
) -> None:
    tracker = make_tracker()
    install_fake_factory(monkeypatch, tracker)
    pool = XiaohongshuBrowserPool(root=tmp_path)
    await publish(pool, 7, 1, "first")
    finished = threading.Event()

    def slow_capture(_context, **_kwargs):
        time.sleep(0.04)
        finished.set()
        return {}

    monkeypatch.setattr(pool_module, "capture_tweet", slow_capture)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            pool.capture_tweet(tweet_id="123", username="user", expected_text="hi"),
            timeout=0.01,
        )
    assert finished.is_set()
    assert pool.size == 0
    assert tracker["closes"] == 1
    await pool.close()
