import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.logging import JsonFormatter
from app.services.xhs_verification import verification_image_path
from app.xhs_cli_compat import (
    IMAGE_INPUT_TIMEOUT_SECONDS,
    PUBLISH_URL,
    _arm_image_upload_tracker,
    _arm_publish_diagnostics,
    _click_element,
    _click_publish,
    _diagnostic_text,
    _diagnostic_url,
    _find_element,
    _find_image_input,
    _is_image_publish_url,
    _is_image_upload_request,
    _log_stage,
    _log_upload_diagnostics,
    _open_creator_publish_page,
    _publish_diagnostics_snapshot,
    _publish_page_feedback,
    _save_verification_screenshot,
    _security_verification_visible,
    _wait_for_image_input,
    _wait_for_image_uploads,
    _wait_for_publish_button,
    publish_note_compat,
)


class FakeElement:
    def __init__(
        self,
        *,
        label: str = "",
        visible: bool = True,
        tag: str = "div",
        attributes: dict[str, str] | None = None,
        fail_click: bool = False,
    ) -> None:
        self.label = label
        self.visible = visible
        self.tag = tag
        self.attributes = attributes or {}
        self.fail_click = fail_click
        self.clicked = False
        self.scrolled = False
        self.evaluated: list[str] = []
        self.evaluate_result: object | None = None
        self.click_options: list[dict[str, object]] = []
        self.disposed = False
        self.handle_result: FakeElement | None = None

    def inner_text(self) -> str:
        return self.label

    def is_visible(self) -> bool:
        return self.visible

    def click(self, **kwargs: object) -> None:
        self.click_options.append(kwargs)
        if self.fail_click:
            raise RuntimeError("outside viewport")
        self.clicked = True

    def scroll_into_view_if_needed(self, **_kwargs: object) -> None:
        self.scrolled = True

    def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)

    def evaluate(self, script: str) -> object:
        self.evaluated.append(script)
        if "tagName.toLowerCase" in script:
            return self.tag
        if self.evaluate_result is not None:
            return self.evaluate_result
        if "el.click()" in script:
            self.clicked = True
        return self.tag

    def evaluate_handle(self, _script: str) -> "FakeHandle":
        return FakeHandle(self.handle_result)

    def screenshot(self, *, path: str) -> None:
        Path(path).write_bytes(b"cropped-verification-panel")

    def dispose(self) -> None:
        self.disposed = True

    def bounding_box(self) -> dict[str, float]:
        return {"x": 10, "y": 20, "width": 100, "height": 40}


class FakeHandle:
    def __init__(self, element: FakeElement | None) -> None:
        self.element = element
        self.disposed = False

    def as_element(self) -> FakeElement | None:
        return self.element

    def dispose(self) -> None:
        self.disposed = True


class FakeRoot:
    def __init__(self, elements: dict[str, list[FakeElement]] | None = None) -> None:
        self.elements = elements or {}

    def query_selector_all(self, selector: str) -> list[FakeElement]:
        return self.elements.get(selector, [])


class FakeKeyboard:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def press(self, key: str) -> None:
        self.keys.append(key)


class FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []
        self.moves: list[tuple[float, float, int]] = []
        self.actions: list[tuple[str, str]] = []

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))

    def move(self, x: float, y: float, *, steps: int) -> None:
        self.moves.append((x, y, steps))

    def down(self, *, button: str) -> None:
        self.actions.append(("down", button))

    def up(self, *, button: str) -> None:
        self.actions.append(("up", button))


class FakePage(FakeRoot):
    def __init__(self, elements: dict[str, list[FakeElement]] | None = None) -> None:
        super().__init__(elements)
        self.main_frame = self
        self.frames = [self]
        self.keyboard = FakeKeyboard()
        self.mouse = FakeMouse()
        self.evaluate_result: object | None = None
        self.scripts: list[str] = []
        self.listeners: dict[str, list[object]] = {}

    def evaluate(self, _script: str) -> object:
        return self.evaluate_result or {"width": 1280, "height": 720}

    def add_init_script(self, *, script: str) -> None:
        self.scripts.append(script)

    def on(self, event: str, callback: object) -> None:
        self.listeners.setdefault(event, []).append(callback)

    def remove_listener(self, event: str, callback: object) -> None:
        self.listeners[event].remove(callback)

    def text_content(self, _selector: str) -> str:
        return ""


@pytest.fixture
def input_wait_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0, on_sleep=None)

    def sleep(seconds):
        clock.now += seconds
        if clock.on_sleep is not None:
            clock.on_sleep()

    monkeypatch.setattr("app.xhs_cli_compat.time.monotonic", lambda: clock.now)
    monkeypatch.setattr("app.xhs_cli_compat.time.sleep", sleep)
    return clock


@pytest.mark.parametrize("ready_at", [0, 21])
def test_image_input_wait_returns_as_soon_as_component_is_ready(
    ready_at, input_wait_clock, monkeypatch,
) -> None:
    image = FakeElement(attributes={"accept": "image/*"})
    page = FakePage({'input[type="file"]': [image]})
    page.url = PUBLISH_URL
    original_query = page.query_selector_all
    monkeypatch.setattr(
        page, "query_selector_all",
        lambda selector: original_query(selector) if input_wait_clock.now >= ready_at else [],
    )

    assert _wait_for_image_input(page, IMAGE_INPUT_TIMEOUT_SECONDS) is image
    assert ready_at <= input_wait_clock.now <= ready_at + 0.3
    assert all(not listeners for listeners in page.listeners.values())


def test_image_input_timeout_logs_bounded_metadata_without_page_content(
    input_wait_clock, caplog, capsys,
) -> None:
    page = FakePage()
    page.url = "https://user:password-secret@creator.xiaohongshu.com/publish?token=page-secret"
    page.evaluate_result = "interactive"
    frame = FakePage()
    frame.url = "https://creator.xiaohongshu.com/frame?session=frame-secret#fragment-secret"
    frame.evaluate_result = "complete"
    data_frame = FakePage()
    data_frame.url = "data:text/html,private-page-content"
    data_frame.evaluate_result = "loading"
    page.frames.extend([frame, data_frame])

    def emit_network_events():
        input_wait_clock.on_sleep = None
        for _ in range(20):
            page.listeners["response"][0](SimpleNamespace(
                request=SimpleNamespace(resource_type="xhr"), status=401,
                url="https://creator.xiaohongshu.com/api/account?token=auth-secret",
            ))
            page.listeners["requestfailed"][0](SimpleNamespace(
                method="GET", resource_type="script",
                url="https://fe-static.xhscdn.com/script.js?signature=signed-secret",
                failure="NS_ERROR_NET_RESET authorization: Bearer bearer-secret "
                "Cookie: session=cookie-secret" + "x" * 600,
            ))

    input_wait_clock.on_sleep = emit_network_events
    with caplog.at_level(logging.INFO):
        assert _wait_for_image_input(page, IMAGE_INPUT_TIMEOUT_SECONDS, admin_id=7) is None
    assert input_wait_clock.now == IMAGE_INPUT_TIMEOUT_SECONDS
    record = next(r for r in caplog.records if getattr(r, "stage", "") ==
                  "image_input_wait_failed")
    assert record.admin_id == 7
    assert record.reason == "timeout"
    assert record.elapsed_seconds == IMAGE_INPUT_TIMEOUT_SECONDS
    assert record.page_state == {
        "frame_count": 3,
        "frames": [
            {"index": 0, "main_frame": True,
             "url": "https://creator.xiaohongshu.com/publish",
             "ready_state": "interactive", "file_input_count": 0},
            {"index": 1, "main_frame": False,
             "url": "https://creator.xiaohongshu.com/frame",
             "ready_state": "complete", "file_input_count": 0},
            {"index": 2, "main_frame": False, "url": "data:",
             "ready_state": "loading", "file_input_count": 0},
        ],
    }
    assert len(record.document_or_auth_responses) == 12
    assert record.document_or_auth_responses[0]["status"] == 401
    assert len(record.failed_requests) == 12
    assert "NS_ERROR_NET_RESET" in record.failed_requests[0]["failure"]
    assert len(record.failed_requests[0]["failure"]) <= 500
    output = capsys.readouterr().err + JsonFormatter().format(record)
    assert "secret" not in output
    assert "private-page-content" not in output
    assert all(not listeners for listeners in page.listeners.values())


@pytest.mark.parametrize("redirect_in", ["page", "iframe"])
def test_image_input_wait_stops_on_async_creator_login_redirect(
    redirect_in, input_wait_clock, caplog, capsys,
) -> None:
    page = FakePage()
    page.url = PUBLISH_URL
    frame = FakePage()
    frame.url = "about:blank"
    page.frames.append(frame)

    def redirect():
        if input_wait_clock.now >= 2:
            target = page if redirect_in == "page" else frame
            target.url = "https://creator.xiaohongshu.com/login?token=redirect-secret"

    input_wait_clock.on_sleep = redirect
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="登录态已失效"):
        _wait_for_image_input(page, IMAGE_INPUT_TIMEOUT_SECONDS)
    assert 2 <= input_wait_clock.now < 3
    record = next(r for r in caplog.records if getattr(r, "stage", "") ==
                  "image_input_wait_failed")
    assert record.reason == "login_redirect"
    assert "redirect-secret" not in capsys.readouterr().err
    assert all(not listeners for listeners in page.listeners.values())


@pytest.mark.parametrize("failure", ["timeout", "login"])
def test_component_wait_failure_stops_publish_before_upload(
    failure, input_wait_clock, tmp_path, monkeypatch, caplog,
) -> None:
    page = FakePage()
    page.url = PUBLISH_URL
    image = tmp_path / "image.png"
    image.write_bytes(b"test-image")
    monkeypatch.setattr("app.xhs_cli_compat._open_creator_publish_page", lambda *a, **kw: None)

    def unexpected_upload(*args, **kwargs):
        pytest.fail("Upload tracking must not start before the component is ready")

    monkeypatch.setattr("app.xhs_cli_compat._arm_image_upload_tracker", unexpected_upload)
    if failure == "login":
        input_wait_clock.on_sleep = lambda: setattr(
            page, "url", "https://creator.xiaohongshu.com/login",
        )
    expected_error = "上传控件超时（45 秒）" if failure == "timeout" else "登录态已失效"
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match=expected_error):
        publish_note_compat(SimpleNamespace(_page=page), "title", [str(image)])
    assert all(getattr(r, "stage", "") != "image_upload_started" for r in caplog.records)
    assert all(not listeners for listeners in page.listeners.values())


def test_image_input_wait_cleans_up_if_browser_query_fails(input_wait_clock, monkeypatch) -> None:
    page = FakePage()
    page.url = PUBLISH_URL

    def browser_error(*args, **kwargs):
        raise RuntimeError("Browser closed")

    monkeypatch.setattr("app.xhs_cli_compat._find_image_input", browser_error)
    with pytest.raises(RuntimeError, match="Browser closed"):
        _wait_for_image_input(page, IMAGE_INPUT_TIMEOUT_SECONDS)
    assert input_wait_clock.now == 0
    assert all(not listeners for listeners in page.listeners.values())


def test_image_input_wait_diagnostics_preserve_error_if_page_closes(
    input_wait_clock, monkeypatch, caplog,
) -> None:
    page = FakePage()
    page.url = PUBLISH_URL

    def closed_frames(*args, **kwargs):
        raise ValueError("frames unavailable")

    def browser_error(*args, **kwargs):
        monkeypatch.setattr("app.xhs_cli_compat._roots", closed_frames)
        raise RuntimeError("Browser closed")

    monkeypatch.setattr("app.xhs_cli_compat._find_image_input", browser_error)
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError, match="Browser closed"):
        _wait_for_image_input(page, IMAGE_INPUT_TIMEOUT_SECONDS)
    record = next(r for r in caplog.records if getattr(r, "stage", "") ==
                  "image_input_wait_failed")
    assert record.reason == "browser_error"
    assert record.page_state == {"snapshot_error": "ValueError"}
    assert all(not listeners for listeners in page.listeners.values())


@pytest.mark.parametrize("failure", ["browser", "login", "timeout"])
def test_image_input_wait_preserves_outcome_if_failure_logging_raises(
    failure, input_wait_clock, monkeypatch,
) -> None:
    page = FakePage()
    page.url = PUBLISH_URL

    def broken_log(stage, *args, **kwargs):
        if stage == "image_input_wait_failed":
            raise OSError("log pipe unavailable")

    def browser_error(*args, **kwargs):
        raise RuntimeError("Browser closed")

    monkeypatch.setattr("app.xhs_cli_compat._log_stage", broken_log)
    if failure == "browser":
        monkeypatch.setattr("app.xhs_cli_compat._find_image_input", browser_error)
    elif failure == "login":
        page.url = "https://creator.xiaohongshu.com/login"
    if failure == "timeout":
        assert _wait_for_image_input(page, IMAGE_INPUT_TIMEOUT_SECONDS) is None
    else:
        error = "Browser closed" if failure == "browser" else "登录态已失效"
        with pytest.raises(RuntimeError, match=error):
            _wait_for_image_input(page, IMAGE_INPUT_TIMEOUT_SECONDS)
    assert all(not listeners for listeners in page.listeners.values())


def test_image_input_wait_detects_login_redirect_during_selector_query(
    input_wait_clock, monkeypatch,
) -> None:
    page = FakePage()
    page.url = PUBLISH_URL

    def find_then_redirect(*args, **kwargs):
        page.url = "https://creator.xiaohongshu.com/login"
        return FakeElement(attributes={"accept": "image/*"})

    monkeypatch.setattr("app.xhs_cli_compat._find_image_input", find_then_redirect)
    with pytest.raises(RuntimeError, match="登录态已失效"):
        _wait_for_image_input(page, IMAGE_INPUT_TIMEOUT_SECONDS)
    assert input_wait_clock.now == 0
    assert all(not listeners for listeners in page.listeners.values())


def test_image_publish_url_requires_image_target() -> None:
    assert _is_image_publish_url(
        "https://creator.xiaohongshu.com/publish/publish?from=tab_switch&target=image"
    )
    assert not _is_image_publish_url(
        "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch"
    )


def test_creator_navigation_uses_sdk_checks_and_removes_listeners(caplog) -> None:
    page = FakePage()
    calls = []

    def goto(url, **kwargs):
        calls.append((url, kwargs))
        page.url = url
        response = SimpleNamespace(
            request=SimpleNamespace(resource_type="document", frame=page),
            url=url, status=200,
        )
        page.listeners["response"][0](response)

    with caplog.at_level(logging.INFO):
        _open_creator_publish_page(SimpleNamespace(_page=page, _goto=goto), admin_id=7)
    assert len(calls) == 1
    assert calls[0][0] == PUBLISH_URL
    assert calls[0][1]["context"] == "loading creator publish page"
    record = next(r for r in caplog.records if getattr(r, "stage", "") == "page_ready")
    assert record.admin_id == 7
    assert record.document_responses[0]["status"] == 200
    assert record.elapsed_seconds >= 0
    assert "导航完成，等待上传组件" in record.message
    assert all(not listeners for listeners in page.listeners.values())


@pytest.mark.parametrize("failure", ["timeout", "risk", "login", "wrong_target"])
def test_creator_navigation_failures_stop_before_upload_and_log_safely(
    failure, tmp_path, caplog, capsys
) -> None:
    page = FakePage()
    page.url = PUBLISH_URL
    page.evaluate_result = "interactive"
    image = tmp_path / "image.png"
    image.write_bytes(b"test-image")
    calls = []

    def goto(url, **kwargs):
        calls.append(url)
        if failure == "timeout":
            page.listeners["requestfailed"][0](SimpleNamespace(
                method="GET", resource_type="script",
                url="https://fe-static.xhscdn.com/script.js?signature=signed-secret",
                failure="NS_ERROR_NET_RESET authorization: Bearer bearer-secret",
            ))
            raise TimeoutError("Page.goto: Timeout 30000ms exceeded")
        if failure == "risk":
            raise RuntimeError("SDK risk-control check rejected page")
        page.url = (
            "https://creator.xiaohongshu.com/login?token=redirect-secret"
            if failure == "login" else "https://creator.xiaohongshu.com/publish?token=redirect-secret"
        )

    with caplog.at_level(logging.INFO), pytest.raises((TimeoutError, RuntimeError)):
        publish_note_compat(
            SimpleNamespace(_page=page, _goto=goto), "title", [str(image)], admin_id=7,
        )
    assert calls == [PUBLISH_URL]
    stages = [getattr(r, "stage", "") for r in caplog.records]
    assert "creator_navigation_failed" in stages
    assert "image_upload_started" not in stages
    assert "page_ready" not in stages
    record = next(r for r in caplog.records if getattr(r, "stage", "") ==
                  "creator_navigation_failed")
    assert record.ready_state == "interactive"
    if failure == "timeout":
        assert "NS_ERROR_NET_RESET" in record.failed_requests[0]["failure"]
    output = capsys.readouterr().err + "".join(JsonFormatter().format(r) for r in caplog.records)
    for secret in ("signed-secret", "bearer-secret", "redirect-secret"):
        assert secret not in output
    assert all(not listeners for listeners in page.listeners.values())


def test_stage_log_is_structured_and_written_to_stderr(capsys) -> None:
    _log_stage("title_filled", "加入标题成功", character_count=12)

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err.startswith("XHS_STAGE ")
    assert '"stage": "title_filled"' in output.err
    assert '"character_count": 12' in output.err


def test_stage_log_reaches_worker_file_and_runtime_tail(tmp_path, capsys) -> None:
    from app.services.runtime_logs import read_tail

    logger = logging.getLogger("app.xhs_cli_compat")
    path = tmp_path / "camoufox-worker.log"
    handler = logging.FileHandler(path)
    handler.setFormatter(JsonFormatter())
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        _log_stage("image_upload_started", "照片已提交至页面", image_count=1)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        handler.close()
    record = json.loads(read_tail(path, 200)[0])
    assert record["stage"] == "image_upload_started"
    assert record["image_count"] == 1
    assert capsys.readouterr().err.startswith("XHS_STAGE ")


def test_upload_failure_logs_real_browser_reason_and_safe_snapshot(caplog, capsys) -> None:
    page = FakePage()
    page.url = "https://creator.xiaohongshu.com/publish?token=page-secret"
    page.evaluate_result = {
        "previewCount": 1, "fileInputCount": 1, "loadingVisible": True,
        "statusCodes": ["upload_in_progress"], "errorCodes": [],
        "arbitraryPageText": "private-page-text",
    }
    tracker = _arm_image_upload_tracker(page, admin_id=7)
    tracker["successfulUrls"].add("https://ros-upload-d4.xhscdn.com/first")
    request = SimpleNamespace(
        method="PUT", resource_type="xhr",
        url="https://ros-upload-d4.xhscdn.com/second?signature=signed-secret",
        failure="NS_ERROR_NET_RESET https://ros-upload-d4.xhscdn.com/?token=failed-secret "
        "authorization: Bearer jwt-secret Cookie: session=cookie-secret; a1=another-secret",
    )
    with caplog.at_level(logging.INFO):
        tracker["requestFailedHandler"](request)
        with pytest.raises(RuntimeError, match="NS_ERROR_NET_RESET") as error:
            _wait_for_image_uploads(
                page, tracker, expected_count=2, timeout_seconds=0.1, settle_seconds=0,
            )
    record = next(r for r in caplog.records if getattr(r, "stage", "") ==
                  "image_upload_diagnostics")
    assert record.admin_id == 7
    assert record.expected == 2
    assert record.completed == 1
    assert record.elapsed_seconds >= 0
    assert record.requests[-1]["method"] == "PUT"
    assert record.requests[-1]["resource_type"] == "xhr"
    assert "NS_ERROR_NET_RESET" in record.requests[-1]["failure"]
    assert record.page_state["previewCount"] == 1
    assert record.page_state["loadingVisible"] is True
    all_output = str(error.value) + capsys.readouterr().err + "".join(
        JsonFormatter().format(r) for r in caplog.records
    )
    for secret in (
        "page-secret", "signed-secret", "failed-secret", "jwt-secret", "cookie-secret",
        "another-secret", "private-page-text",
    ):
        assert secret not in all_output
    assert all(not listeners for listeners in page.listeners.values())


def test_upload_preflight_is_observed_without_counting_or_failing_upload(caplog) -> None:
    page = FakePage()
    tracker = _arm_image_upload_tracker(page)
    request = SimpleNamespace(
        method="OPTIONS", url="https://ros-upload-d4.xhscdn.com/?token=secret",
        failure="NS_ERROR_FAILURE", resource_type="xhr",
    )
    with caplog.at_level(logging.INFO):
        tracker["requestFailedHandler"](request)
        tracker["responseHandler"](SimpleNamespace(request=request, url=request.url, status=403))
    assert len(tracker["observed"]) == 2
    assert tracker["failures"] == []
    assert tracker["successfulUrls"] == set()
    assert any(getattr(r, "stage", "") == "image_upload_http_error" for r in caplog.records)
    assert all(not r["recognized"] for r in tracker["observed"])
    tracker["responseHandler"](SimpleNamespace(
        request=SimpleNamespace(method="PUT", url=request.url), url=request.url, status=200,
    ))
    _wait_for_image_uploads(page, tracker, expected_count=1, timeout_seconds=0.1, settle_seconds=0)
    assert all(not listeners for listeners in page.listeners.values())


def test_upload_browser_errors_keep_only_categories_and_safe_endpoints() -> None:
    page = FakePage()
    tracker = _arm_image_upload_tracker(page)
    handler = tracker["browserErrorHandler"]
    handler(SimpleNamespace(text="private normal console text"))
    for _ in range(20):
        handler(SimpleNamespace(text="CORS private text cookie=session-secret "
                                "https://ros-upload-d4.xhscdn.com/?signature=signed-secret"))
    page.listeners["pageerror"][0](RuntimeError("NS_ERROR_NET_RESET private-page-error"))
    assert len(tracker["browserErrors"]) == 12
    assert tracker["browserErrors"][0] == {
        "category": "cors", "endpoints": ["https://ros-upload-d4.xhscdn.com/"],
    }
    assert tracker["browserErrors"][-1] == {"category": "network", "endpoints": []}
    output = json.dumps(tracker["browserErrors"])
    assert "private" not in output
    assert "secret" not in output


@pytest.mark.parametrize("mode", ["http", "timeout", "dom"])
def test_upload_failure_modes_log_summary_and_remove_all_listeners(mode, caplog) -> None:
    page = FakePage()
    page.evaluate_result = {"errorCodes": ["upload_failed"] if mode == "dom" else []}
    tracker = _arm_image_upload_tracker(page)
    if mode == "http":
        request = SimpleNamespace(method="POST", url="https://ros-upload-d4.xhscdn.com/")
        tracker["responseHandler"](SimpleNamespace(request=request, url=request.url, status=500))
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        _wait_for_image_uploads(
            page, tracker, expected_count=1, timeout_seconds=0.01, settle_seconds=0,
        )
    assert any(getattr(r, "stage", "") == "image_upload_diagnostics" for r in caplog.records)
    assert all(not listeners for listeners in page.listeners.values())


def test_upload_snapshot_exception_and_url_credentials_are_redacted(caplog) -> None:
    page = FakePage()
    tracker = _arm_image_upload_tracker(page)
    snapshot = {"snapshotError": "failed https://user:password-secret@example.com/?token=secret"}
    with caplog.at_level(logging.INFO):
        _log_upload_diagnostics(page, tracker, snapshot, 1, "test")
    output = JsonFormatter().format(caplog.records[-1])
    assert "https://example.com/" in output
    assert "password-secret" not in output
    assert "token=secret" not in output


def test_find_element_skips_hidden_candidate() -> None:
    hidden = FakeElement(visible=False)
    visible = FakeElement()
    page = FakePage({"input": [hidden, visible]})

    assert _find_element(page, ["input"], visible=True) is visible


def test_find_image_input_ignores_video_upload() -> None:
    video = FakeElement(attributes={"accept": "video/mp4"})
    image = FakeElement(attributes={"accept": ".jpg,.jpeg,.png,.webp"})
    page = FakePage({'input[type="file"]': [video, image]})

    assert _find_image_input(page) is image


def test_wait_for_image_uploads_requires_successful_cdn_puts() -> None:
    page = FakePage()
    tracker = _arm_image_upload_tracker(page)
    first = SimpleNamespace(
        request=SimpleNamespace(
            method="PUT",
            url="https://ros-upload-d4.xhscdn.com/spectrum/first",
        ),
        status=200,
        url="https://ros-upload-d4.xhscdn.com/spectrum/first",
    )
    second = SimpleNamespace(
        request=SimpleNamespace(
            method="PUT",
            url="https://ros-upload-d4.xhscdn.com/spectrum/second",
        ),
        status=200,
        url="https://ros-upload-d4.xhscdn.com/spectrum/second",
    )
    tracker["responseHandler"](first)
    tracker["responseHandler"](second)

    _wait_for_image_uploads(
        page,
        tracker,
        expected_count=2,
        timeout_seconds=0.1,
        settle_seconds=0,
    )

    assert len(tracker["successfulUrls"]) == 2
    assert page.listeners["response"] == []
    assert page.listeners["requestfailed"] == []


def test_wait_for_image_uploads_rejects_failed_cdn_put() -> None:
    page = FakePage()
    tracker = _arm_image_upload_tracker(page)
    failed = SimpleNamespace(
        request=SimpleNamespace(
            method="PUT",
            url="https://ros-upload-d4.xhscdn.com/spectrum/failed",
        ),
        status=500,
        url="https://ros-upload-d4.xhscdn.com/spectrum/failed",
    )
    tracker["responseHandler"](failed)

    try:
        _wait_for_image_uploads(
            page,
            tracker,
            expected_count=1,
            timeout_seconds=0.1,
            settle_seconds=0,
        )
    except RuntimeError as exc:
        assert "图片上传失败" in str(exc)
        assert "HTTP 500" in str(exc)
    else:
        raise AssertionError("failed image upload should stop publishing")


def test_image_upload_request_accepts_changed_method_and_known_upload_path() -> None:
    assert _is_image_upload_request(
        SimpleNamespace(
            method="POST",
            url="https://edith.xiaohongshu.com/api/media/upload?token=secret",
        )
    )
    assert not _is_image_upload_request(
        SimpleNamespace(
            method="POST",
            url="https://t2.xiaohongshu.com/api/v2/collect",
        )
    )


def test_wait_for_image_uploads_accepts_stable_editor_dom() -> None:
    page = FakePage()
    page.evaluate_result = {
        "titleVisible": True,
        "contentVisible": True,
        "previewCount": 1,
        "loadingVisible": False,
        "fileInputCount": 1,
        "statusCodes": [],
        "errorCodes": [],
    }
    tracker = _arm_image_upload_tracker(page)

    _wait_for_image_uploads(
        page,
        tracker,
        expected_count=1,
        timeout_seconds=0.1,
        settle_seconds=0,
    )

    assert page.listeners["response"] == []
    assert page.listeners["requestfailed"] == []


def test_wait_for_image_uploads_does_not_accept_editor_without_preview() -> None:
    page = FakePage()
    page.url = "https://creator.xiaohongshu.com/publish/publish?target=image"
    page.evaluate_result = {
        "titleVisible": True,
        "contentVisible": True,
        "previewCount": 0,
        "loadingVisible": False,
        "fileInputCount": 1,
        "statusCodes": [],
        "errorCodes": [],
    }
    tracker = _arm_image_upload_tracker(page)

    try:
        _wait_for_image_uploads(
            page,
            tracker,
            expected_count=1,
            timeout_seconds=0.01,
            settle_seconds=0,
        )
    except RuntimeError as exc:
        assert "等待图片上传完成" in str(exc)
    else:
        raise AssertionError("editor without a loaded preview is not upload completion")


def test_wait_for_image_uploads_rejects_visible_dom_failure() -> None:
    page = FakePage()
    page.evaluate_result = {
        "titleVisible": True,
        "contentVisible": True,
        "previewCount": 0,
        "loadingVisible": False,
        "fileInputCount": 1,
        "statusCodes": ["upload_failed"],
        "errorCodes": ["upload_failed"],
    }
    tracker = _arm_image_upload_tracker(page)

    try:
        _wait_for_image_uploads(
            page,
            tracker,
            expected_count=1,
            timeout_seconds=0.1,
            settle_seconds=0,
        )
    except RuntimeError as exc:
        assert "图片上传失败" in str(exc)
    else:
        raise AssertionError("visible upload failure should stop publishing")

    assert page.listeners["response"] == []
    assert page.listeners["requestfailed"] == []


def test_image_upload_timeout_has_safe_diagnostics() -> None:
    page = FakePage()
    page.url = "https://creator.xiaohongshu.com/publish/publish?token=secret"
    page.evaluate_result = {
        "titleVisible": False,
        "contentVisible": False,
        "previewCount": 0,
        "loadingVisible": True,
        "fileInputCount": 1,
        "statusCodes": ["upload_in_progress"],
        "errorCodes": [],
    }
    tracker = _arm_image_upload_tracker(page)
    response = SimpleNamespace(
        request=SimpleNamespace(
            method="POST",
            url="https://edith.xiaohongshu.com/api/prepare?token=secret",
        ),
        status=200,
        url="https://edith.xiaohongshu.com/api/prepare?token=secret",
    )
    tracker["responseHandler"](response)

    try:
        _wait_for_image_uploads(
            page,
            tracker,
            expected_count=1,
            timeout_seconds=0.01,
            settle_seconds=0,
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "网络确认 0/1" in message
        assert "https://creator.xiaohongshu.com/publish/publish" in message
        assert "https://edith.xiaohongshu.com/api/prepare" in message
        assert "secret" not in message
        assert "upload_in_progress" in message
    else:
        raise AssertionError("upload timeout should include diagnostics")

    assert page.listeners["response"] == []
    assert page.listeners["requestfailed"] == []


def test_click_custom_publish_button_dispatches_native_publish_event() -> None:
    page = FakePage()
    button = FakeElement(tag="xhs-publish-btn")
    button.evaluate_result = {
        "dispatched": True,
        "method": "component-method",
        "submitDisabled": "false",
        "submitLoading": "false",
    }

    _click_publish(page, button)

    event_script = button.evaluated[-1]
    assert "el._onPublish()" in event_script
    assert "new CustomEvent('publish'" in event_script
    assert "bubbles: true" in event_script
    assert "composed: true" in event_script
    assert page.mouse.moves == []


def test_click_custom_publish_button_rejects_disabled_component() -> None:
    page = FakePage()
    button = FakeElement(tag="xhs-publish-btn")
    button.evaluate_result = {
        "dispatched": False,
        "submitDisabled": "true",
        "submitLoading": "false",
    }

    try:
        _click_publish(page, button)
    except RuntimeError as exc:
        assert "submit-disabled='true'" in str(exc)
    else:
        raise AssertionError("disabled publish component should be rejected")


def test_click_custom_publish_button_rejects_loading_component() -> None:
    page = FakePage()
    button = FakeElement(tag="xhs-publish-btn")
    button.evaluate_result = {
        "dispatched": False,
        "submitDisabled": "false",
        "submitLoading": "true",
    }

    try:
        _click_publish(page, button)
    except RuntimeError as exc:
        assert "submit-loading='true'" in str(exc)
    else:
        raise AssertionError("loading publish component should be rejected")


def test_wait_for_publish_button_prefers_real_red_button() -> None:
    widget = FakeElement(tag="xhs-publish-btn")
    real_button = FakeElement(tag="button", label="发布")
    page = FakePage(
        {
            'xhs-publish-btn[is-publish="true"]': [widget],
            ".publish-page-publish-btn button.bg-red": [real_button],
        }
    )

    assert _wait_for_publish_button(page, timeout_seconds=0.1) is real_button


def test_publish_diagnostics_redacts_url_query_and_collects_snapshot() -> None:
    page = FakePage()
    button = FakeElement(tag="xhs-publish-btn")
    button.evaluate_result = {
        "hasComponentPublishMethod": True,
        "userIdPresent": True,
        "bindPhone": True,
    }

    diagnostics = _arm_publish_diagnostics(page, button)
    snapshot = _publish_diagnostics_snapshot(page, button, diagnostics)

    assert snapshot["initial"]["hasComponentPublishMethod"] is True
    assert page.listeners["response"] == []
    assert page.listeners["requestfailed"] == []
    assert page.listeners["console"] == []
    assert page.listeners["pageerror"] == []
    assert _diagnostic_url(
        "https://creator.xiaohongshu.com/api/publish?token=secret#fragment"
    ) == "https://creator.xiaohongshu.com/api/publish"
    assert _diagnostic_text("cookie=session-secret token:abc") == (
        "cookie=*** token:***"
    )


def test_publish_diagnostics_detects_security_verification_response() -> None:
    page = FakePage()
    button = FakeElement(tag="xhs-publish-btn")
    diagnostics = _arm_publish_diagnostics(page, button)
    response = SimpleNamespace(
        request=SimpleNamespace(method="POST"),
        status=461,
        url="https://edith.xiaohongshu.com/web_api/sns/v2/note",
    )

    diagnostics["responseHandler"](response)

    assert diagnostics["state"]["securityRequired"] is True
    assert diagnostics["state"]["publishResponseStatus"] == 461


def test_click_element_falls_back_to_dom_when_outside_viewport() -> None:
    element = FakeElement(fail_click=True)

    _click_element(element, "点击测试元素")

    assert element.scrolled is True
    assert element.clicked is True
    assert any("scrollIntoView" in script for script in element.evaluated)


def test_publish_page_feedback_returns_visible_messages() -> None:
    page = FakePage()
    page.evaluate_result = ["标题不能为空", "图片上传失败"]

    assert _publish_page_feedback(page) == "标题不能为空；图片上传失败"


def test_security_verification_visible_uses_visible_challenge_text() -> None:
    marker = FakeElement(label="Scan to verify")
    page = FakePage({"text=Scan to verify": [marker]})

    assert _security_verification_visible(page) is True


def test_verification_screenshot_prefers_cropped_panel(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("XHS_UPLOAD_DIR", str(tmp_path))
    panel = FakeElement()
    marker = FakeElement(label="Scan to verify")
    marker.handle_result = panel
    page = FakePage({"text=Scan to verify": [marker]})

    assert _save_verification_screenshot(page, 42) is True
    assert verification_image_path(42).read_bytes() == b"cropped-verification-panel"
