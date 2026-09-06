from pathlib import Path
from types import SimpleNamespace

from app.services.xhs_verification import verification_image_path
from app.xhs_cli_compat import (
    _arm_image_upload_tracker,
    _arm_publish_diagnostics,
    _click_element,
    _click_publish,
    _diagnostic_text,
    _diagnostic_url,
    _find_element,
    _find_image_input,
    _is_image_publish_url,
    _publish_diagnostics_snapshot,
    _publish_page_feedback,
    _save_verification_screenshot,
    _security_verification_visible,
    _wait_for_image_uploads,
    _wait_for_publish_button,
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


def test_image_publish_url_requires_image_target() -> None:
    assert _is_image_publish_url(
        "https://creator.xiaohongshu.com/publish/publish?from=tab_switch&target=image"
    )
    assert not _is_image_publish_url(
        "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch"
    )


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
