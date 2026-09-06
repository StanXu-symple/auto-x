import struct
import zlib

from app.xhs_cli_compat import (
    _click_element,
    _click_publish,
    _find_element,
    _find_image_input,
    _find_red_button_position,
    _publish_page_feedback,
    _select_image_text_tab,
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
        self.screenshot_result = b""

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

    def bounding_box(self) -> dict[str, float]:
        return {"x": 10, "y": 20, "width": 100, "height": 40}

    def screenshot(self, **_kwargs: object) -> bytes:
        return self.screenshot_result


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

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))


class FakePage(FakeRoot):
    def __init__(self, elements: dict[str, list[FakeElement]] | None = None) -> None:
        super().__init__(elements)
        self.main_frame = self
        self.frames = [self]
        self.keyboard = FakeKeyboard()
        self.mouse = FakeMouse()
        self.evaluate_result: object | None = None

    def evaluate(self, _script: str) -> object:
        return self.evaluate_result


def test_select_image_text_tab_by_visible_label() -> None:
    video = FakeElement(label="上传视频")
    image = FakeElement(label="上传图文")
    page = FakePage({"div.creator-tab": [video, image]})

    _select_image_text_tab(page)

    assert image.clicked is True
    assert video.clicked is False
    assert page.keyboard.keys == ["Escape"]


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


def test_click_custom_publish_button_uses_dom_button() -> None:
    page = FakePage()
    button = FakeElement(tag="xhs-publish-btn")
    button.evaluate_result = {"clicked": True, "target": "发布"}

    _click_publish(page, button)

    assert page.mouse.clicks == []
    assert any("el.shadowRoot" in script for script in button.evaluated)
    assert any("立即发布" in script for script in button.evaluated)


def _png_with_red_rectangle(
    width: int,
    height: int,
    rectangle: tuple[int, int, int, int],
) -> bytes:
    left, top, right, bottom = rectangle
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            color = (255, 36, 66) if left <= x <= right and top <= y <= bottom else (255, 255, 255)
            rows.extend(color)

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows)))
        + chunk(b"IEND", b"")
    )


def test_find_red_button_position_uses_largest_red_region() -> None:
    png = _png_with_red_rectangle(100, 40, (60, 8, 91, 31))

    assert _find_red_button_position(png) == {"x": 75.5, "y": 19.5}


def test_click_closed_custom_publish_button_uses_red_button_position() -> None:
    page = FakePage()
    button = FakeElement(tag="xhs-publish-btn")
    button.evaluate_result = {"clicked": False, "target": "XHS-PUBLISH-BTN"}
    button.screenshot_result = _png_with_red_rectangle(100, 40, (60, 8, 91, 31))

    _click_publish(page, button)

    assert button.scrolled is True
    assert button.clicked is True
    assert button.click_options == [
        {"timeout": 5000, "force": True, "position": {"x": 75.5, "y": 19.5}}
    ]
    assert page.mouse.clicks == []


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
