from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

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
PUBLISH_DIAGNOSTIC_LIMIT = 12
SENSITIVE_DIAGNOSTIC_PATTERN = re.compile(
    r"(?i)(a1|web_session|cookie|authorization|token)(\s*[\"']?\s*[:=]\s*[\"']?)"
    r"([^\s,;\"']+)"
)


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
                        submit_loading = element.get_attribute("submit-loading")
                        disabled = element.get_attribute("disabled")
                        aria_disabled = element.get_attribute("aria-disabled")
                        tag_name = element.evaluate("el => el.tagName.toLowerCase()")
                        label = " ".join((element.inner_text() or "").split())[:120]
                    except Exception:
                        submit_disabled = submit_loading = disabled = aria_disabled = None
                        tag_name = label = "unknown"
                    if (
                        submit_disabled != "true"
                        and submit_loading != "true"
                        and disabled is None
                        and aria_disabled != "true"
                    ):
                        logger.warning(
                            "Selected Xiaohongshu publish control: selector=%s "
                            "tag=%s text=%r submit_disabled=%r submit_loading=%r",
                            selector,
                            tag_name,
                            label,
                            submit_disabled,
                            submit_loading,
                        )
                        return element
        time.sleep(0.3)
    return None


def _diagnostic_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))[:300]


def _diagnostic_text(value: Any) -> str:
    return SENSITIVE_DIAGNOSTIC_PATTERN.sub(r"\1\2***", str(value))[:500]


def _arm_publish_diagnostics(page: Any, element: Any) -> dict[str, Any]:
    network: list[dict[str, str | int]] = []
    console: list[dict[str, str]] = []

    def record_response(response: Any) -> None:
        try:
            request = response.request
            method = str(request.method).upper()
            if method == "GET":
                return
            hostname = (urlparse(response.url).hostname or "").lower()
            if not hostname.endswith(("xiaohongshu.com", "xhscdn.com")):
                return
            if len(network) < PUBLISH_DIAGNOSTIC_LIMIT:
                network.append(
                    {
                        "method": method,
                        "status": int(response.status),
                        "url": _diagnostic_url(str(response.url)),
                    }
                )
        except Exception:
            return

    def record_request_failure(request: Any) -> None:
        try:
            method = str(request.method).upper()
            hostname = (urlparse(request.url).hostname or "").lower()
            if method == "GET" or not hostname.endswith(
                ("xiaohongshu.com", "xhscdn.com")
            ):
                return
            if len(network) < PUBLISH_DIAGNOSTIC_LIMIT:
                network.append(
                    {
                        "method": method,
                        "status": "failed",
                        "url": _diagnostic_url(str(request.url)),
                    }
                )
        except Exception:
            return

    def record_console(message: Any) -> None:
        try:
            message_type = str(message.type).lower()
            if message_type not in {"warning", "error"}:
                return
            if len(console) < PUBLISH_DIAGNOSTIC_LIMIT:
                console.append(
                    {
                        "type": message_type,
                        "text": _diagnostic_text(message.text),
                    }
                )
        except Exception:
            return

    def record_page_error(error: Any) -> None:
        if len(console) < PUBLISH_DIAGNOSTIC_LIMIT:
            console.append({"type": "pageerror", "text": _diagnostic_text(error)})

    try:
        page.on("response", record_response)
        page.on("requestfailed", record_request_failure)
        page.on("console", record_console)
        page.on("pageerror", record_page_error)
    except Exception as exc:
        logger.warning("Unable to monitor Xiaohongshu publish page: %s", exc)

    try:
        browser_state = element.evaluate(
            """el => {
                const key = '__xsentinelPublishDiagnostics';
                const existing = window[key];
                if (existing?.observer) existing.observer.disconnect();
                const state = {
                    startedAt: Date.now(),
                    eventCount: 0,
                    eventTrusted: null,
                    attributes: [],
                    messages: [],
                    errors: [],
                    observer: null,
                };
                window[key] = state;
                document.addEventListener('publish', event => {
                    state.eventCount += 1;
                    state.eventTrusted = event.isTrusted;
                }, {capture: true, once: true});
                const addMessage = value => {
                    const text = String(value || '').replace(/\\s+/g, ' ').trim();
                    if (text && text.length <= 300 && !state.messages.includes(text)) {
                        state.messages.push(text);
                        if (state.messages.length > 12) state.messages.shift();
                    }
                };
                state.observer = new MutationObserver(records => {
                    for (const record of records) {
                        if (record.type === 'attributes' && record.target === el) {
                            state.attributes.push({
                                name: record.attributeName,
                                value: el.getAttribute(record.attributeName),
                            });
                        }
                        for (const node of record.addedNodes || []) {
                            if (node.nodeType === Node.TEXT_NODE) addMessage(node.textContent);
                            else if (node.nodeType === Node.ELEMENT_NODE) {
                                addMessage(node.innerText || node.textContent);
                            }
                        }
                    }
                    if (state.attributes.length > 12) {
                        state.attributes = state.attributes.slice(-12);
                    }
                });
                state.observer.observe(document.body, {
                    attributes: true,
                    attributeFilter: ['submit-disabled', 'submit-loading'],
                    childList: true,
                    subtree: true,
                });
                window.addEventListener('error', event => {
                    addMessage(event.message);
                    state.errors.push(String(event.message || 'page error').slice(0, 300));
                }, {once: true});
                window.addEventListener('unhandledrejection', event => {
                    const reason = event.reason?.message || event.reason || 'unhandled rejection';
                    addMessage(reason);
                    state.errors.push(String(reason).slice(0, 300));
                }, {once: true});
                let userInfo = {};
                try {
                    userInfo = JSON.parse(localStorage.getItem('USER_INFO') || '{}');
                } catch (_) {}
                userInfo = userInfo?.user?.value || userInfo?.userInfo || userInfo;
                return {
                    hasComponentPublishMethod: typeof el._onPublish === 'function',
                    submitDisabled: el.getAttribute('submit-disabled'),
                    submitLoading: el.getAttribute('submit-loading'),
                    userIdPresent: Boolean(userInfo.id || userInfo.userId),
                    bindPhone: typeof userInfo.bindPhone === 'boolean'
                        ? userInfo.bindPhone : null,
                };
            }"""
        )
    except Exception as exc:
        browser_state = {"setupError": str(exc)[:300]}
    return {
        "network": network,
        "console": console,
        "browserState": browser_state,
        "responseHandler": record_response,
        "requestFailedHandler": record_request_failure,
        "consoleHandler": record_console,
        "pageErrorHandler": record_page_error,
    }


def _publish_diagnostics_snapshot(
    page: Any, element: Any, diagnostics: dict[str, Any]
) -> dict[str, Any]:
    try:
        browser_state = element.evaluate(
            """el => {
                const state = window.__xsentinelPublishDiagnostics || {};
                state.observer?.disconnect();
                return {
                    eventCount: state.eventCount || 0,
                    eventTrusted: state.eventTrusted ?? null,
                    attributes: state.attributes || [],
                    messages: state.messages || [],
                    errors: state.errors || [],
                    finalSubmitDisabled: el.getAttribute('submit-disabled'),
                    finalSubmitLoading: el.getAttribute('submit-loading'),
                };
            }"""
        )
    except Exception as exc:
        browser_state = {"snapshotError": str(exc)[:300]}
    for event, key in (
        ("response", "responseHandler"),
        ("requestfailed", "requestFailedHandler"),
        ("console", "consoleHandler"),
        ("pageerror", "pageErrorHandler"),
    ):
        try:
            page.remove_listener(event, diagnostics[key])
        except Exception:
            pass
    return {
        "initial": diagnostics.get("browserState", {}),
        "browser": browser_state,
        "network": diagnostics.get("network", []),
        "console": diagnostics.get("console", []),
    }


def _click_publish(page: Any, element: Any) -> None:
    try:
        tag_name = str(element.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:
        tag_name = ""
    if tag_name == "xhs-publish-btn":
        try:
            result = element.evaluate(
                """el => {
                    const submitDisabled = el.getAttribute('submit-disabled');
                    const submitLoading = el.getAttribute('submit-loading');
                    if (submitDisabled === 'true' || submitLoading === 'true') {
                        return {
                            dispatched: false,
                            submitDisabled,
                            submitLoading,
                        };
                    }
                    let method = 'component-method';
                    if (typeof el._onPublish === 'function') {
                        el._onPublish();
                    } else {
                        method = 'event-fallback';
                        el.dispatchEvent(new CustomEvent('publish', {
                            bubbles: true,
                            composed: true,
                        }));
                    }
                    return {
                        dispatched: true,
                        method,
                        submitDisabled,
                        submitLoading,
                    };
                }"""
            )
            if not isinstance(result, dict) or not result.get("dispatched"):
                state = result if isinstance(result, dict) else {"result": result}
                raise RuntimeError(
                    "小红书发布组件当前不可用："
                    f"submit-disabled={state.get('submitDisabled')!r}, "
                    f"submit-loading={state.get('submitLoading')!r}"
                )
            logger.warning(
                "Triggered Xiaohongshu publish component: method=%s "
                "event=publish bubbles=true composed=true",
                result.get("method"),
            )
            return
        except Exception as exc:
            raise RuntimeError(f"触发小红书发布事件失败：{exc}") from exc
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
    diagnostics = _arm_publish_diagnostics(page, publish_button)
    try:
        _click_publish(page, publish_button)
    except Exception:
        diagnostic_snapshot = _publish_diagnostics_snapshot(
            page, publish_button, diagnostics
        )
        logger.warning("Xiaohongshu publish diagnostics: %s", diagnostic_snapshot)
        raise

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
            _publish_diagnostics_snapshot(page, publish_button, diagnostics)
            result = {"success": True, "note_id": note_id, "url": current_url}
            return result if return_detail else True
        feedback = _publish_page_feedback(page)
        if feedback:
            last_feedback = feedback
        time.sleep(0.5)
    current_url = page.url or ""
    diagnostic_snapshot = _publish_diagnostics_snapshot(
        page, publish_button, diagnostics
    )
    logger.warning("Xiaohongshu publish diagnostics: %s", diagnostic_snapshot)
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
