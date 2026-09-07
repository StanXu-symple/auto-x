from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.xhs_cli_compat import publish_note_compat

logger = logging.getLogger(__name__)


def _close_client(client: Any) -> None:
    """Close an xhs-cli client and its Camoufox context defensively."""
    if client is None:
        return
    context = getattr(client, "_camoufox_ctx", None)
    try:
        close = getattr(client, "close", None)
        if callable(close):
            close()
            return
        if context is not None:
            context.__exit__(None, None, None)
    except Exception:
        logger.warning("Unable to close Xiaohongshu browser session", exc_info=True)


def _create_persistent_client(
    cookie_dict: dict[str, str], profile_dir: Path, cookie_version: int
) -> Any:
    """Create an xhs-cli client backed by a persistent Camoufox profile.

    xhs-cli exposes a synchronous client.  The pool runs each client on its own
    single-thread executor so Playwright's sync API never crosses threads.
    """
    from camoufox.sync_api import Camoufox
    from xhs_cli.client import XhsClient

    XhsClient.publish_note = publish_note_compat

    class PersistentXhsClient(XhsClient):
        def start(self) -> None:
            profile_dir.mkdir(parents=True, exist_ok=True)
            profile_dir.chmod(0o700)
            self._camoufox_ctx = Camoufox(
                headless=True,
                persistent_context=True,
                user_data_dir=str(profile_dir),
            )
            self._browser = self._camoufox_ctx.__enter__()
            self._page = self._browser.new_page()
            version_path = profile_dir.parent / ".browser-profile-cookie-version"
            try:
                seeded_version = int(version_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                seeded_version = None
            existing_names = {
                str(cookie.get("name") or "")
                for cookie in self._page.context.cookies()
                if str(cookie.get("domain") or "").endswith("xiaohongshu.com")
            }
            if (
                seeded_version != cookie_version
                or not {"a1", "web_session"}.issubset(existing_names)
            ):
                cookies = [
                    {
                        "name": key,
                        "value": value,
                        "domain": ".xiaohongshu.com",
                        "path": "/",
                    }
                    for key, value in cookie_dict.items()
                ]
                if cookies:
                    self._page.context.add_cookies(cookies)
                version_path.write_text(str(cookie_version), encoding="ascii")
                version_path.chmod(0o600)
            self._goto(
                "https://www.xiaohongshu.com",
                timeout=20000,
                wait_min=1,
                wait_max=2,
                context="establishing persistent browser session",
            )

        def close(self) -> None:
            context = getattr(self, "_camoufox_ctx", None)
            self._camoufox_ctx = None
            self._browser = None
            self._page = None
            if context is not None:
                context.__exit__(None, None, None)

    return PersistentXhsClient(cookie_dict)


@dataclass(slots=True)
class _BrowserLease:
    admin_id: int
    cookie_version: int
    profile_dir: Path
    executor: ThreadPoolExecutor
    client: Any | None = None
    busy: bool = False
    last_used: float = field(default_factory=time.monotonic)


class XiaohongshuBrowserPool:
    """Bounded persistent Camoufox sessions for Xiaohongshu publishing."""

    def __init__(
        self,
        *,
        root: Path,
        max_browsers: int = 1,
        max_concurrency: int = 1,
    ) -> None:
        self.root = root
        self.max_browsers = max_browsers
        self.max_concurrency = max_concurrency
        self._condition = asyncio.Condition()
        self._leases: dict[int, _BrowserLease] = {}
        self._business_slots = asyncio.Semaphore(max_concurrency)
        self._retire_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def size(self) -> int:
        return len(self._leases)

    @property
    def busy_count(self) -> int:
        return sum(lease.busy for lease in self._leases.values())

    async def _acquire(self, admin_id: int, cookie_version: int) -> _BrowserLease:
        async with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("小红书浏览器池已关闭")
                existing = self._leases.get(admin_id)
                if existing is not None:
                    if existing.cookie_version == cookie_version and not existing.busy:
                        existing.busy = True
                        existing.last_used = time.monotonic()
                        return existing
                    if existing.busy:
                        await self._condition.wait()
                        continue
                    self._leases.pop(admin_id, None)
                    await self._close_lease(existing)
                    continue

                if len(self._leases) < self.max_browsers:
                    lease = _BrowserLease(
                        admin_id=admin_id,
                        cookie_version=cookie_version,
                        profile_dir=self.root / "users" / str(admin_id) / "browser-profile",
                        executor=ThreadPoolExecutor(
                            max_workers=1,
                            thread_name_prefix=f"xhs-browser-{admin_id}",
                        ),
                        busy=True,
                    )
                    self._leases[admin_id] = lease
                    return lease

                idle = [lease for lease in self._leases.values() if not lease.busy]
                if idle:
                    evicted = min(idle, key=lambda lease: lease.last_used)
                    self._leases.pop(evicted.admin_id, None)
                    await self._close_lease(evicted)
                    continue
                await self._condition.wait()

    async def _close_lease(self, lease: _BrowserLease) -> None:
        client = lease.client
        lease.client = None
        if client is not None:
            await asyncio.get_running_loop().run_in_executor(lease.executor, _close_client, client)
        lease.executor.shutdown(wait=False, cancel_futures=True)

    async def _release(self, lease: _BrowserLease) -> None:
        async with self._condition:
            if self._leases.get(lease.admin_id) is lease:
                lease.busy = False
                lease.last_used = time.monotonic()
                self._condition.notify_all()

    async def _discard(self, lease: _BrowserLease) -> None:
        await self._detach(lease)
        await self._close_lease(lease)

    async def _detach(self, lease: _BrowserLease) -> None:
        async with self._condition:
            if self._leases.get(lease.admin_id) is lease:
                self._leases.pop(lease.admin_id, None)
            lease.busy = False
            self._condition.notify_all()

    def _retire_after(
        self, lease: _BrowserLease, future: asyncio.Future[Any]
    ) -> None:
        async def retire() -> None:
            try:
                await future
            except BaseException:
                pass
            await self._close_lease(lease)

        task = asyncio.create_task(retire())
        self._retire_tasks.add(task)
        task.add_done_callback(self._retire_tasks.discard)

    async def invalidate(self, admin_id: int) -> None:
        async with self._condition:
            lease = self._leases.get(admin_id)
            if lease is None:
                return
            while lease.busy:
                await self._condition.wait()
            if self._leases.get(admin_id) is not lease:
                return
            self._leases.pop(admin_id, None)
            await self._close_lease(lease)
            self._condition.notify_all()

    async def publish(
        self,
        *,
        admin_id: int,
        cookie_version: int,
        cookie_dict: dict[str, str],
        title: str,
        content: str,
        image_paths: list[str],
    ) -> dict[str, Any]:
        async with self._business_slots:
            lease = await self._acquire(admin_id, cookie_version)
            try:
                loop = asyncio.get_running_loop()

                def publish(client: Any) -> dict[str, Any]:
                    page = getattr(client, "_page", None)
                    if client is None or page is None or page.is_closed():
                        _close_client(client)
                        client = _create_persistent_client(
                            cookie_dict, lease.profile_dir, lease.cookie_version
                        )
                        lease.client = client
                        client.start()
                    return client.publish_note(
                        title=title,
                        image_paths=image_paths,
                        content=content,
                        return_detail=True,
                        admin_id=lease.admin_id,
                    )

                if lease.client is None:
                    def create_and_publish(_: Any) -> dict[str, Any]:
                        client = _create_persistent_client(
                            cookie_dict, lease.profile_dir, lease.cookie_version
                        )
                        lease.client = client
                        client.start()
                        return client.publish_note(
                            title=title,
                            image_paths=image_paths,
                            content=content,
                            return_detail=True,
                            admin_id=lease.admin_id,
                        )

                    future = loop.run_in_executor(
                        lease.executor, create_and_publish, None
                    )
                else:
                    future = loop.run_in_executor(
                        lease.executor, publish, lease.client
                    )
                try:
                    result = await asyncio.shield(future)
                except asyncio.CancelledError:
                    await self._detach(lease)
                    self._retire_after(lease, future)
                    raise
                if isinstance(result, dict):
                    return result
                return {"success": bool(result)}
            except Exception:
                logger.warning(
                    "Discarding failed Xiaohongshu browser session",
                    extra={"admin_id": admin_id},
                    exc_info=True,
                )
                await self._discard(lease)
                raise
            finally:
                if self._leases.get(admin_id) is lease:
                    await self._release(lease)

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            leases = list(self._leases.values())
            self._leases.clear()
            for lease in leases:
                lease.busy = False
            self._condition.notify_all()
        await asyncio.gather(
            *(self._close_lease(lease) for lease in leases),
            return_exceptions=True,
        )
        if self._retire_tasks:
            await asyncio.gather(*self._retire_tasks, return_exceptions=True)
