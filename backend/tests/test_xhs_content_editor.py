import logging
import traceback
from types import SimpleNamespace

import pytest

from app import xhs_cli_compat as module
from app.core.logging import JsonFormatter


class EditorNode:
    def __init__(self, **state):
        self.state = {
            "tag": "div", "className": "tiptap ProseMirror", "role": "textbox",
            "contenteditable": "true", "isContentEditable": True,
            "readonly": False, "disabled": False,
            "visible": True, "enabled": True, "editable": True,
            **state,
        }
        self.text = ""
        self.on_click = None
        self.on_fill = None
        self.on_read = None
        self.focused = True
        self.fills = []
        self.clicks = 0


class EditorLocator:
    """Resolve against mutable DOM state on every action, like a Locator."""

    def __init__(self, root, selector, index=None):
        self.root = root
        self.selector = selector
        self.index = index

    @property
    def node(self):
        return self.root.elements[self.selector][self.index]

    def count(self):
        return len(self.root.elements.get(self.selector, []))

    def nth(self, index):
        return EditorLocator(self.root, self.selector, index)

    def evaluate(self, script, *, timeout):
        assert timeout > 0
        if "getSelection" in script:
            return self.node.focused
        return dict(self.node.state)

    def is_visible(self):
        return self.node.state["visible"]

    def is_enabled(self, *, timeout):
        return self.node.state["enabled"]

    def is_editable(self, *, timeout):
        return self.node.state["editable"]

    def click(self, *, timeout):
        assert 0 < timeout <= module.CONTENT_ACTION_TIMEOUT_SECONDS * 1000
        node = self.node
        node.clicks += 1
        if node.on_click:
            node.on_click()

    def fill(self, text, *, timeout):
        assert 0 < timeout <= module.CONTENT_ACTION_TIMEOUT_SECONDS * 1000
        node = self.node
        node.fills.append(text)
        if node.on_fill:
            node.on_fill(text)
        else:
            node.text = text

    def inner_text(self, *, timeout):
        node = self.node
        if node.on_read:
            node.on_read()
        return node.text

    def press(self, key, *, timeout):
        assert key == "ControlOrMeta+A"
        self.root.page.selected = self.node


class EditorPage:
    def __init__(self, nodes=()):
        self.page = self
        self.main_frame = self
        self.frames = [self]
        self.elements = {module.CONTENT_SELECTORS[0]: list(nodes)}
        self.url = module.PUBLISH_URL
        self.keyboard = SimpleNamespace(insert_text=self.insert_text)
        self.insertions = []
        self.selected = None

    def locator(self, selector):
        return EditorLocator(self, selector)

    def insert_text(self, text):
        assert self.selected is not None
        self.insertions.append(text)
        self.selected.text = text


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=0.0, on_sleep=None)

    def sleep(seconds):
        value.now += seconds
        if value.on_sleep:
            value.on_sleep()

    monkeypatch.setattr(module.time, "monotonic", lambda: value.now)
    monkeypatch.setattr(module.time, "sleep", sleep)
    return value


@pytest.mark.parametrize("bad_state", [
    {"visible": False}, {"enabled": False}, {"editable": False},
    {"isContentEditable": False}, {"readonly": True}, {"disabled": True},
])
def test_fill_skips_non_actionable_candidates(bad_state, clock):
    bad, good = EditorNode(**bad_state), EditorNode()
    module._fill_content(EditorPage([bad, good]), "正文\n第二段 📱")
    assert not bad.fills and bad.clicks == 0
    assert good.text == "正文\n第二段 📱"


def test_waits_until_editor_is_editable(clock):
    editor = EditorNode(editable=False, isContentEditable=False)

    def ready_later():
        if clock.now >= 1:
            editor.state.update(editable=True, isContentEditable=True)

    clock.on_sleep = ready_later
    module._fill_content(EditorPage([editor]), "正文")
    assert clock.now >= 1
    assert editor.fills == ["正文"]


def test_relocates_after_click_replaces_editor_and_selector(clock):
    old, new = EditorNode(), EditorNode()
    page = EditorPage([old])

    def replace():
        page.elements[module.CONTENT_SELECTORS[0]] = []
        page.elements[module.CONTENT_SELECTORS[1]] = [new]

    old.on_click = replace
    module._fill_content(page, "新正文")
    assert not old.fills
    assert new.fills == ["新正文"]


def test_relocates_after_fill_failure(clock):
    old, new = EditorNode(), EditorNode()
    page = EditorPage([old])

    def replace(_text):
        page.elements[module.CONTENT_SELECTORS[0]] = [new]
        raise RuntimeError("detached old editor")

    old.on_fill = replace
    module._fill_content(page, "正文")
    assert old.fills == ["正文"]
    assert new.fills == ["正文"]
    assert not page.insertions


def test_verifies_current_editor_instead_of_detached_written_node(clock):
    old, new = EditorNode(), EditorNode()
    page = EditorPage([old])

    def replace(text):
        old.text = text
        page.elements[module.CONTENT_SELECTORS[0]] = [new]

    old.on_fill = replace
    module._fill_content(page, "正文")
    assert new.fills == ["正文"]


def test_keyboard_fallback_replaces_partial_fill_instead_of_appending(clock):
    editor = EditorNode()
    page = EditorPage([editor])

    def partial_fill(text):
        editor.text = text[:2]
        raise RuntimeError("fill failed after partial input")

    editor.on_fill = partial_fill
    module._fill_content(page, "完整正文 📱")
    assert len(editor.fills) == 2
    assert page.insertions == ["完整正文 📱"]
    assert editor.text == "完整正文 📱"


def test_keyboard_fallback_requires_focus_and_selection_in_editor(clock):
    editor = EditorNode()
    editor.focused = False
    editor.on_fill = lambda text: None
    page = EditorPage([editor])
    with pytest.raises(RuntimeError, match="尚未点击发布"):
        module._fill_content(page, "正文")
    assert not page.insertions


def test_ambiguous_editors_are_not_filled_and_wait_is_bounded(clock):
    first, second = EditorNode(), EditorNode()
    with pytest.raises(RuntimeError, match="尚未点击发布"):
        module._fill_content(EditorPage([first, second]), "正文")
    assert not first.fills and not second.fills
    assert clock.now == pytest.approx(module.CONTENT_INPUT_TIMEOUT_SECONDS)


def test_editor_in_child_frame_is_supported(clock):
    page, frame = EditorPage(), EditorPage([EditorNode()])
    frame.page = page
    page.frames.append(frame)
    module._fill_content(page, "正文")
    assert frame.elements[module.CONTENT_SELECTORS[0]][0].text == "正文"


def test_diagnostics_report_readonly_state_without_body_or_url_secrets(clock, caplog, capsys):
    editor = EditorNode(readonly=True)
    page = EditorPage([editor])
    page.url = "https://user:password@example.com/publish?token=secret"
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        module._fill_content(page, "private-article-body")
    output = capsys.readouterr().err + "\n".join(
        JsonFormatter().format(record) for record in caplog.records
    )
    assert '"readonly": true' in output
    assert "private-article-body" not in output
    assert "password" not in output and "token=secret" not in output


def test_playwright_fill_error_does_not_leak_article_in_traceback(clock, caplog, capsys):
    editor = EditorNode()
    page = EditorPage([editor])

    def error(text):
        raise RuntimeError(f'Locator.fill: fill("{text}") timeout')

    editor.on_fill = error
    page.keyboard.insert_text = error
    content = "private-article-body"
    with pytest.raises(RuntimeError) as failure:
        module._fill_content(page, content)
    output = "".join(traceback.format_exception(failure.value))
    output += capsys.readouterr().err + caplog.text
    assert content not in output


@pytest.mark.parametrize("actual,expected,matched", [
    ("中文\n\n第二段 📱", "中文\n第二段 📱", True),
    ("hello\u00a0world", "hello world", True),
    ("helloworld", "hello world", False),
    ("正文正文", "正文", False),
    ("截断", "截断的正文", False),
])
def test_readback_detects_truncation_and_duplication(actual, expected, matched, clock):
    editor = EditorNode()
    editor.text = actual
    assert module._verify_content(EditorPage([editor]), expected, 10) is matched


def test_verification_waits_for_stable_content_after_render(clock):
    editor = EditorNode()
    editor.text = "正文"
    clock.on_sleep = lambda: setattr(editor, "text", "")
    assert not module._verify_content(EditorPage([editor]), "正文", 10)
