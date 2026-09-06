from app.xhs_cli_compat import (
    _click_element,
    _click_publish,
    _find_element,
    _find_image_input,
    _is_image_publish_url,
    _publish_page_feedback,
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

    def dispose(self) -> None:
        self.disposed = True

    def bounding_box(self) -> dict[str, float]:
        return {"x": 10, "y": 20, "width": 100, "height": 40}

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

    def evaluate(self, _script: str) -> object:
        return self.evaluate_result or {"width": 1280, "height": 720}

    def add_init_script(self, *, script: str) -> None:
        self.scripts.append(script)


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


def test_click_custom_publish_button_dispatches_native_publish_event() -> None:
    page = FakePage()
    button = FakeElement(tag="xhs-publish-btn")
    button.evaluate_result = {
        "dispatched": True,
        "submitDisabled": "false",
        "submitLoading": "false",
    }

    _click_publish(page, button)

    event_script = button.evaluated[-1]
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
