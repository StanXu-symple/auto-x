from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official"
IMAGE_INPUT_SELECTORS = (
    "input.upload-input",
    'input[type="file"][accept*="image"]',
    'input[type="file"][accept*=".jpg"]',
    'input[type="file"]',
)
TITLE_SELECTORS = (
    "div.d-input input",
    'input[placeholder*="填写标题"]',
    'input[placeholder*="标题"]',
    'input[class*="title"]',
    'textarea[class*="title"]',
)
CONTENT_SELECTORS = (
    'div[role="textbox"][contenteditable="true"]',
    'div.tiptap.ProseMirror[contenteditable="true"]',
    'div.ProseMirror[contenteditable="true"]',
    "div.ql-editor",
    '[contenteditable="true"]',
)
PUBLISH_BUTTON_SELECTORS = (
    'xhs-publish-btn[is-publish="true"]',
    "xhs-publish-btn:not([is-publish])",
    ".publish-page-publish-btn button.bg-red",
    'button:has-text("发布")',
    '[class*="publish-btn"]',
)
IMAGE_ACCEPT_MARKERS = ("image/", ".jpg", ".jpeg", ".png", ".webp", ".heic")


def _roots(page: Any) -> Iterable[Any]:
    yield page
    for frame in page.frames:
        if frame is not page.main_frame:
            yield frame


def _is_visible(element: Any) -> bool:
    try:
        return bool(element.is_visible())
    except Exception:
        return False


def _find_element(page: Any, selectors: Iterable[str], *, visible: bool) -> Any | None:
    for root in _roots(page):
        for selector in selectors:
            try:
                elements = root.query_selector_all(selector)
            except Exception:
                continue
            for element in elements:
                if not visible or _is_visible(element):
                    return element
    return None


def _wait_for_element(
    page: Any,
    selectors: Iterable[str],
    *,
    visible: bool,
    timeout_seconds: float,
) -> Any | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        element = _find_element(page, selectors, visible=visible)
        if element is not None:
            return element
        time.sleep(0.3)
    return None


def _find_image_input(page: Any) -> Any | None:
    candidates: list[Any] = []
    for root in _roots(page):
        for selector in IMAGE_INPUT_SELECTORS:
            try:
                elements = root.query_selector_all(selector)
            except Exception:
                continue
            for element in elements:
                if element not in candidates:
                    candidates.append(element)
    for element in candidates:
        try:
            accept = (element.get_attribute("accept") or "").lower()
        except Exception:
            continue
        if any(marker in accept for marker in IMAGE_ACCEPT_MARKERS):
            return element
    return candidates[0] if len(candidates) == 1 else None


def _wait_for_image_input(page: Any, timeout_seconds: float) -> Any | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        element = _find_image_input(page)
        if element is not None:
            return element
        time.sleep(0.3)
    return None


def _select_image_text_tab(page: Any) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        for root in _roots(page):
            try:
                tabs = root.query_selector_all("div.creator-tab")
            except Exception:
                continue
            for tab in tabs:
                try:
                    label = (tab.inner_text() or "").strip()
                except Exception:
                    continue
                if label not in {"上传图文", "图文发布"} or not _is_visible(tab):
                    continue
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                try:
                    tab.click()
                except Exception:
                    tab.click(force=True)
                logger.info("Selected Xiaohongshu image-text publish tab")
                return
        time.sleep(0.3)
    raise RuntimeError("找不到小红书创作中心的“上传图文”页签，页面结构可能已更新")


def _wait_for_publish_button(page: Any, timeout_seconds: float) -> Any | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        element = _find_element(page, PUBLISH_BUTTON_SELECTORS, visible=True)
        if element is not None:
            try:
                submit_disabled = element.get_attribute("submit-disabled")
                disabled = element.get_attribute("disabled")
            except Exception:
                submit_disabled = disabled = None
            if submit_disabled != "true" and disabled is None:
                return element
        time.sleep(0.3)
    return None


def _click_publish(page: Any, element: Any) -> None:
    try:
        tag_name = str(element.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:
        tag_name = ""
    if tag_name == "xhs-publish-btn":
        box = element.bounding_box()
        if not box:
            raise RuntimeError("小红书发布按钮没有可点击区域")
        page.mouse.click(box["x"] + box["width"] * 0.65, box["y"] + box["height"] / 2)
        return
    element.click()


def publish_note_compat(
    client: Any,
    title: str,
    image_paths: list[str],
    content: str = "",
    return_detail: bool = False,
) -> bool | dict[str, str | bool]:
    for path in image_paths:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Image not found: {path}")

    page = client._page
    client._goto(
        PUBLISH_URL,
        timeout=30000,
        wait_min=2,
        wait_max=3,
        context="loading creator publish page",
    )
    if "/login" in (page.url or "").lower():
        raise RuntimeError("小红书创作中心登录态已失效，请更新登录态")

    _select_image_text_tab(page)
    image_input = _wait_for_image_input(page, timeout_seconds=15)
    if image_input is None:
        raise RuntimeError("找不到图文图片上传控件，页面结构可能已更新")
    image_input.set_input_files(image_paths)

    title_input = _wait_for_element(
        page,
        TITLE_SELECTORS,
        visible=True,
        timeout_seconds=60,
    )
    if title_input is None:
        raise RuntimeError("图片上传后找不到标题输入框，请检查上传结果或页面结构")
    title_input.fill(title)

    if content:
        content_input = _wait_for_element(
            page,
            CONTENT_SELECTORS,
            visible=True,
            timeout_seconds=15,
        )
        if content_input is None:
            raise RuntimeError("找不到正文输入框，页面结构可能已更新")
        content_input.fill(content)

    publish_button = _wait_for_publish_button(page, timeout_seconds=15)
    if publish_button is None:
        raise RuntimeError("发布按钮不可点击，请检查标题、正文和图片是否通过页面校验")
    _click_publish(page, publish_button)

    deadline = time.monotonic() + 20
    note_id = ""
    while time.monotonic() < deadline:
        current_url = page.url or ""
        page_text = page.text_content("body") or ""
        note_id = (
            client._extract_note_id_from_url(current_url)
            or client._extract_note_id_from_page()
        )
        if client._is_publish_success(page_text, current_url, note_id):
            result = {"success": True, "note_id": note_id, "url": current_url}
            return result if return_detail else True
        time.sleep(0.5)
    raise RuntimeError("点击发布后未检测到成功状态，请在创作中心检查页面校验提示")


def main() -> None:
    from xhs_cli.cli import cli
    from xhs_cli.client import XhsClient

    XhsClient.publish_note = publish_note_compat
    cli()


if __name__ == "__main__":
    main()
