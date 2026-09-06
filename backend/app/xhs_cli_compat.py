from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

PUBLISH_URL = (
    "https://creator.xiaohongshu.com/publish/publish?from=tab_switch&target=image"
)
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
    ".publish-page-publish-btn button.bg-red",
    'button:text-is("发布")',
    'button:has-text("立即发布")',
    '[role="button"]:text-is("发布")',
    '[class*="publish-btn"]',
    'xhs-publish-btn[is-publish="true"]',
    "xhs-publish-btn:not([is-publish])",
)
IMAGE_ACCEPT_MARKERS = ("image/", ".jpg", ".jpeg", ".png", ".webp", ".heic")
PUBLISH_RESULT_TIMEOUT_SECONDS = 60
SHADOW_ROOT_CAPTURE_SCRIPT = """
(() => {
    const roots = window.__xsentinelShadowRoots || new WeakMap();
    if (!window.__xsentinelShadowRoots) {
        Object.defineProperty(window, '__xsentinelShadowRoots', {
            value: roots,
            configurable: false,
            enumerable: false,
        });
    }
    if (Element.prototype.attachShadow.__xsentinelWrapped) return;
    const originalAttachShadow = Element.prototype.attachShadow;
    const wrappedAttachShadow = function(init) {
        const options = {...init, mode: 'open'};
        const root = originalAttachShadow.call(this, options);
        roots.set(this, root);
        Object.defineProperty(this, '__xsentinelShadowRoot', {
            value: root,
            configurable: false,
            enumerable: false,
        });
        return root;
    };
    Object.defineProperty(wrappedAttachShadow, '__xsentinelWrapped', {value: true});
    Element.prototype.attachShadow = wrappedAttachShadow;
})();
"""


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


def _click_element(element: Any, description: str) -> None:
    errors: list[str] = []
    try:
        element.scroll_into_view_if_needed(timeout=5000)
    except Exception as exc:
        errors.append(f"scroll: {exc}")
    for options in ({"timeout": 5000}, {"timeout": 5000, "force": True}):
        try:
            element.click(**options)
            return
        except Exception as exc:
            errors.append(f"click: {exc}")
    try:
        element.evaluate(
            "el => { el.scrollIntoView({block: 'center', inline: 'center'}); el.click(); }"
        )
        logger.warning("Used DOM click fallback for %s", description)
        return
    except Exception as exc:
        errors.append(f"DOM click: {exc}")
    raise RuntimeError(f"{description}失败：{' | '.join(errors)}")


def _is_image_publish_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path.rstrip("/") == "/publish/publish" and parse_qs(parsed.query).get(
        "target"
    ) == ["image"]


def _wait_for_publish_button(page: Any, timeout_seconds: float) -> Any | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for selector in PUBLISH_BUTTON_SELECTORS:
            for root in _roots(page):
                try:
                    elements = root.query_selector_all(selector)
                except Exception:
                    continue
                for element in elements:
                    if not _is_visible(element):
                        continue
                    try:
                        submit_disabled = element.get_attribute("submit-disabled")
                        disabled = element.get_attribute("disabled")
                        aria_disabled = element.get_attribute("aria-disabled")
                        tag_name = element.evaluate("el => el.tagName.toLowerCase()")
                        label = " ".join((element.inner_text() or "").split())[:120]
                    except Exception:
                        submit_disabled = disabled = aria_disabled = None
                        tag_name = label = "unknown"
                    if (
                        submit_disabled != "true"
                        and disabled is None
                        and aria_disabled != "true"
                    ):
                        logger.warning(
                            "Selected Xiaohongshu publish control: selector=%s "
                            "tag=%s text=%r submit_disabled=%r",
                            selector,
                            tag_name,
                            label,
                            submit_disabled,
                        )
                        return element
        time.sleep(0.3)
    return None


def _install_shadow_root_capture(page: Any) -> None:
    try:
        page.add_init_script(script=SHADOW_ROOT_CAPTURE_SCRIPT)
    except Exception as exc:
        raise RuntimeError(f"安装小红书 Shadow DOM 兼容脚本失败：{exc}") from exc


def _find_shadow_publish_button(element: Any) -> Any | None:
    try:
        handle = element.evaluate_handle(
            """el => {
                const capturedRoots = window.__xsentinelShadowRoots;
                const roots = [
                    el.shadowRoot,
                    el.__xsentinelShadowRoot,
                    capturedRoots?.get(el),
                ].filter(Boolean);
                const candidates = [];
                const visited = new Set();
                while (roots.length) {
                    const root = roots.shift();
                    if (visited.has(root)) continue;
                    visited.add(root);
                    for (const node of root.querySelectorAll('*')) {
                        const openRoot = node.shadowRoot;
                        const exposedRoot = node.__xsentinelShadowRoot;
                        const capturedRoot = capturedRoots?.get(node);
                        if (openRoot) roots.push(openRoot);
                        if (exposedRoot) roots.push(exposedRoot);
                        if (capturedRoot) roots.push(capturedRoot);
                        const role = node.getAttribute?.('role');
                        if (node.tagName === 'BUTTON' || role === 'button') {
                            candidates.push(node);
                        }
                    }
                }
                const normalize = node => (node.innerText || node.textContent || '')
                    .replace(/\\s+/g, ' ').trim();
                return candidates.find(node => {
                    const text = normalize(node);
                    const disabled = node.disabled
                        || node.getAttribute?.('disabled') !== null
                        || node.getAttribute?.('aria-disabled') === 'true';
                    return !disabled && (text === '发布' || text.includes('立即发布'));
                }) || null;
            }"""
        )
    except Exception as exc:
        logger.warning("Unable to access captured Xiaohongshu Shadow DOM: %s", exc)
        return None
    button = handle.as_element()
    if button is None:
        handle.dispose()
        try:
            diagnostics = element.evaluate(
                """el => {
                    const capturedRoots = window.__xsentinelShadowRoots;
                    const root = el.shadowRoot
                        || el.__xsentinelShadowRoot
                        || capturedRoots?.get(el);
                    return {
                        captureInstalled: Boolean(capturedRoots),
                        attachShadowWrapped: Boolean(
                            Element.prototype.attachShadow.__xsentinelWrapped
                        ),
                        hasOpenRoot: Boolean(el.shadowRoot),
                        hasExposedRoot: Boolean(el.__xsentinelShadowRoot),
                        hasCapturedRoot: Boolean(capturedRoots?.get(el)),
                        rootMode: root?.mode || '',
                        controls: root ? Array.from(
                            root.querySelectorAll('button, [role="button"]')
                        ).slice(0, 10).map(node => ({
                            tag: node.tagName,
                            text: (node.innerText || node.textContent || '')
                                .replace(/\\s+/g, ' ').trim().slice(0, 80),
                            disabled: Boolean(node.disabled)
                                || node.getAttribute('aria-disabled') === 'true',
                        })) : [],
                    };
                }"""
            )
        except Exception as exc:
            diagnostics = {"diagnostics_error": str(exc)}
        logger.warning("Xiaohongshu Shadow DOM publish button not found: %s", diagnostics)
        return None
    return button


def _click_publish(page: Any, element: Any) -> None:
    try:
        tag_name = str(element.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:
        tag_name = ""
    if tag_name == "xhs-publish-btn":
        errors: list[str] = []
        shadow_button = _find_shadow_publish_button(element)
        if shadow_button is not None:
            try:
                _click_element(shadow_button, "点击 Shadow DOM 内部发布按钮")
                logger.warning("Clicked the real button inside Xiaohongshu closed Shadow DOM")
                return
            except Exception as exc:
                errors.append(f"captured Shadow DOM button: {exc}")
            finally:
                shadow_button.dispose()
        try:
            clicked = element.evaluate(
                """el => {
                    const roots = [el, el.shadowRoot].filter(Boolean);
                    const candidates = [];
                    while (roots.length) {
                        const root = roots.shift();
                        for (const node of root.querySelectorAll('*')) {
                            if (node.shadowRoot) roots.push(node.shadowRoot);
                            const role = node.getAttribute?.('role');
                            if (node.tagName === 'BUTTON' || role === 'button') {
                                candidates.push(node);
                            }
                        }
                    }
                    const normalize = node => (node.innerText || node.textContent || '')
                        .replace(/\\s+/g, ' ').trim();
                    const button = candidates.find(node => {
                        const text = normalize(node);
                        const disabled = node.disabled
                            || node.getAttribute?.('disabled') !== null
                            || node.getAttribute?.('aria-disabled') === 'true';
                        return !disabled && (text === '发布' || text.includes('立即发布'));
                    });
                    if (!button) {
                        return {clicked: false, target: normalize(el) || el.tagName};
                    }
                    button.scrollIntoView({block: 'center', inline: 'center'});
                    button.click();
                    return {clicked: true, target: normalize(button) || button.tagName};
                }"""
            )
            if clicked and clicked.get("clicked"):
                logger.warning(
                    "Clicked Xiaohongshu publish control via open Shadow DOM: target=%r",
                    clicked.get("target", ""),
                )
                return
        except Exception as exc:
            errors.append(f"Shadow DOM: {exc}")

        try:
            element.scroll_into_view_if_needed(timeout=5000)
        except Exception as exc:
            errors.append(f"scroll: {exc}")
            try:
                element.evaluate(
                    "el => el.scrollIntoView({block: 'center', inline: 'center'})"
                )
            except Exception as dom_exc:
                errors.append(f"DOM scroll: {dom_exc}")

        time.sleep(0.2)
        try:
            box = element.bounding_box()
            if not box or box["width"] <= 0 or box["height"] <= 0:
                raise RuntimeError(f"无有效点击区域：{box}")
            x = box["x"] + box["width"] * 0.65
            y = box["y"] + box["height"] / 2
            viewport = page.evaluate(
                "() => ({width: window.innerWidth, height: window.innerHeight})"
            )
            if not (0 <= x <= viewport["width"] and 0 <= y <= viewport["height"]):
                raise RuntimeError(f"落点 ({x}, {y}) 超出视口 {viewport}")
            page.mouse.move(x, y, steps=24)
            time.sleep(0.25)
            page.mouse.down(button="left")
            time.sleep(0.08)
            page.mouse.up(button="left")
            logger.warning(
                "Clicked Xiaohongshu publish widget with humanized pointer events: "
                "box=%s point=(%s, %s) viewport=%s",
                box,
                x,
                y,
                viewport,
            )
            return
        except Exception as exc:
            errors.append(f"humanized pointer click: {exc}")
            raise RuntimeError(f"点击小红书发布按钮失败：{' | '.join(errors)}") from exc
    _click_element(element, "点击小红书发布按钮")


def _publish_page_feedback(page: Any) -> str:
    try:
        feedback = page.evaluate(
            """() => {
                const selectors = [
                    '[role="alert"]', '[class*="toast"]', '[class*="message"]',
                    '[class*="error"]', '[class*="fail"]', '[class*="tip"]'
                ];
                const texts = [];
                for (const node of document.querySelectorAll(selectors.join(','))) {
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    if (style.display === 'none' || style.visibility === 'hidden'
                            || rect.width === 0 || rect.height === 0) continue;
                    const text = (node.innerText || node.textContent || '')
                        .replace(/\\s+/g, ' ').trim();
                    if (text && text.length <= 300 && !texts.includes(text)) texts.push(text);
                }
                return texts.slice(0, 8);
            }"""
        )
    except Exception as exc:
        logger.warning("Unable to collect Xiaohongshu page feedback", extra={"error": str(exc)})
        return ""
    if not isinstance(feedback, list):
        return ""
    return "；".join(str(item) for item in feedback if item)


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
    _install_shadow_root_capture(page)
    client._goto(
        PUBLISH_URL,
        timeout=30000,
        wait_min=2,
        wait_max=3,
        context="loading creator publish page",
    )
    if "/login" in (page.url or "").lower():
        raise RuntimeError("小红书创作中心登录态已失效，请更新登录态")
    if not _is_image_publish_url(page.url or ""):
        raise RuntimeError(
            "未进入小红书图文发布模式：URL 缺少 target=image；"
            f"当前页面：{page.url or ''}"
        )

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

    time.sleep(1)
    _click_element(title_input, "发布前重新聚焦标题输入框")

    publish_button = _wait_for_publish_button(page, timeout_seconds=15)
    if publish_button is None:
        raise RuntimeError("发布按钮不可点击，请检查标题、正文和图片是否通过页面校验")
    _click_publish(page, publish_button)

    deadline = time.monotonic() + PUBLISH_RESULT_TIMEOUT_SECONDS
    note_id = ""
    last_feedback = ""
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
        feedback = _publish_page_feedback(page)
        if feedback:
            last_feedback = feedback
        time.sleep(0.5)
    current_url = page.url or ""
    detail = f"；页面提示：{last_feedback}" if last_feedback else ""
    raise RuntimeError(
        f"点击发布后 {PUBLISH_RESULT_TIMEOUT_SECONDS} 秒内未检测到成功状态"
        f"；当前页面：{current_url}{detail}"
    )


def main() -> None:
    from xhs_cli.cli import cli
    from xhs_cli.client import XhsClient

    XhsClient.publish_note = publish_note_compat
    cli()


if __name__ == "__main__":
    main()
