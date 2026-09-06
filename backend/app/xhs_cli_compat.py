from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

from app.services.xhs_verification import (
    clear_verification_image,
    cli_admin_id,
    verification_image_path,
)

logger = logging.getLogger(__name__)
STAGE_LOG_PREFIX = "XHS_STAGE "

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
IMAGE_UPLOAD_TIMEOUT_SECONDS = 120
IMAGE_UPLOAD_SETTLE_SECONDS = 3
IMAGE_UPLOAD_DIAGNOSTIC_LIMIT = 12
PUBLISH_RESULT_TIMEOUT_SECONDS = 60
SECURITY_VERIFICATION_TIMEOUT_SECONDS = 90
SECURITY_VERIFICATION_SETTLE_SECONDS = 5
PUBLISH_DIAGNOSTIC_LIMIT = 12
SECURITY_VERIFICATION_TEXT_SELECTORS = (
    "text=Scan to verify",
    "text=Scan with logged-in",
    "text=QR code expires",
    "text=扫码验证",
)
SENSITIVE_DIAGNOSTIC_PATTERN = re.compile(
    r"(?i)(a1|web_session|cookie|authorization|token)(\s*[\"']?\s*[:=]\s*[\"']?)"
    r"([^\s,;\"']+)"
)


def _log_stage(stage: str, message: str, **details: Any) -> None:
    payload = {"level": "INFO", "stage": stage, "message": message, **details}
    sys.stderr.write(
        STAGE_LOG_PREFIX + json.dumps(payload, ensure_ascii=False) + "\n"
    )
    sys.stderr.flush()


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


def _is_image_upload_request(request: Any) -> bool:
    try:
        parsed = urlparse(str(request.url))
        hostname = (parsed.hostname or "").lower()
        path = parsed.path.lower()
        method = str(request.method).upper()
        if method not in {"POST", "PUT"}:
            return False
        if hostname.endswith("xhscdn.com"):
            return "upload" in hostname or any(
                marker in path for marker in ("/spectrum/", "/upload/")
            )
        return hostname.endswith("xiaohongshu.com") and any(
            marker in path
            for marker in ("/upload", "/image/upload", "/media/upload")
        )
    except Exception:
        return False


def _image_upload_dom_snapshot(page: Any) -> dict[str, Any]:
    try:
        snapshot = page.evaluate(
            """() => {
                const visible = node => {
                    if (!node) return false;
                    const style = getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return style.display !== 'none' && style.visibility !== 'hidden'
                        && rect.width > 0 && rect.height > 0;
                };
                const firstVisible = selectors => {
                    for (const selector of selectors) {
                        for (const node of document.querySelectorAll(selector)) {
                            if (visible(node)) return node;
                        }
                    }
                    return null;
                };
                const title = firstVisible([
                    'div.d-input input', 'input[placeholder*="填写标题"]',
                    'input[placeholder*="标题"]', 'input[class*="title"]',
                    'textarea[class*="title"]'
                ]);
                const content = firstVisible([
                    'div[role="textbox"][contenteditable="true"]',
                    'div.tiptap.ProseMirror[contenteditable="true"]',
                    'div.ProseMirror[contenteditable="true"]', '.ql-editor',
                    '[contenteditable="true"]'
                ]);
                const previewSelectors = [
                    '[class*="image-preview"] img', '[class*="img-preview"] img',
                    '[class*="preview-item"] img', '[class*="cover"] img',
                    '[class*="img-list"] img', '[class*="image-list"] img',
                    '[class*="upload"] img'
                ];
                const previews = new Set();
                for (const selector of previewSelectors) {
                    for (const node of document.querySelectorAll(selector)) {
                        if (visible(node) && node.complete && node.naturalWidth > 0) {
                            previews.add(node);
                        }
                    }
                }
                const previewCount = previews.size;
                const loadingVisible = Boolean(firstVisible([
                    '[aria-busy="true"]', '[class*="upload"][class*="loading"]',
                    '[class*="upload"] [class*="loading"]',
                    '[class*="upload"] [class*="progress"]',
                    '[class*="upload"] [class*="spinner"]'
                ]));
                const statusCodes = [];
                const errorCodes = [];
                const inProgressPattern = /(?:图片|照片).{0,12}(?:上传中|处理中)|上传中/;
                const errorPattern = new RegExp(
                    '(?:图片|照片).{0,12}(?:上传失败|处理失败)'
                    + '|上传失败|文件格式不支持'
                );
                for (const node of document.querySelectorAll(
                    '[role="alert"], [class*="toast"], [class*="message"], '
                    + '[class*="error"], [class*="fail"], [class*="tip"]'
                )) {
                    if (!visible(node)) continue;
                    const text = (node.innerText || node.textContent || '').trim();
                    if (errorPattern.test(text)) {
                        if (!statusCodes.includes('upload_failed')) {
                            statusCodes.push('upload_failed');
                            errorCodes.push('upload_failed');
                        }
                    } else if (inProgressPattern.test(text)
                            && !statusCodes.includes('upload_in_progress')) {
                        statusCodes.push('upload_in_progress');
                    }
                }
                return {
                    titleVisible: Boolean(title),
                    contentVisible: Boolean(content),
                    previewCount,
                    loadingVisible,
                    fileInputCount: document.querySelectorAll('input[type="file"]').length,
                    statusCodes,
                    errorCodes,
                };
            }"""
        )
    except Exception as exc:
        return {"snapshotError": _diagnostic_text(exc)}
    return snapshot if isinstance(snapshot, dict) else {"snapshotError": "invalid"}


def _safe_upload_dom_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "titleVisible": bool(snapshot.get("titleVisible")),
        "contentVisible": bool(snapshot.get("contentVisible")),
        "previewCount": int(snapshot.get("previewCount") or 0),
        "loadingVisible": bool(snapshot.get("loadingVisible")),
        "fileInputCount": int(snapshot.get("fileInputCount") or 0),
        "statusCodes": list(snapshot.get("statusCodes", []))[:4],
        "errorCodes": list(snapshot.get("errorCodes", []))[:4],
        **(
            {"snapshotError": _diagnostic_text(snapshot["snapshotError"])}
            if snapshot.get("snapshotError")
            else {}
        ),
    }


def _arm_image_upload_tracker(page: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "successfulUrls": set(),
        "failures": [],
        "observed": [],
    }

    def record_response(response: Any) -> None:
        try:
            request = response.request
            recognized = _is_image_upload_request(request)
            parsed = urlparse(str(response.url))
            hostname = (parsed.hostname or "").lower()
            method = str(request.method).upper()
            if recognized or (
                method != "GET"
                and hostname.endswith(("xiaohongshu.com", "xhscdn.com"))
            ):
                state["observed"].append(
                    {
                        "method": method,
                        "status": int(response.status),
                        "url": _diagnostic_url(str(response.url)),
                        "recognized": recognized,
                    }
                )
                del state["observed"][:-IMAGE_UPLOAD_DIAGNOSTIC_LIMIT]
            if not recognized:
                return
            status = int(response.status)
            url = _diagnostic_url(str(response.url))
            if 200 <= status < 300:
                previous_count = len(state["successfulUrls"])
                state["successfulUrls"].add(url)
                completed = len(state["successfulUrls"])
                if completed > previous_count:
                    _log_stage(
                        "image_upload_progress",
                        "收到照片上传成功响应",
                        completed=completed,
                        method=method,
                        endpoint=url,
                    )
            else:
                state["failures"].append(f"HTTP {status}: {url}")
        except Exception:
            return

    def record_request_failure(request: Any) -> None:
        if _is_image_upload_request(request):
            state["failures"].append(
                f"request failed: {_diagnostic_url(str(request.url))}"
            )

    page.on("response", record_response)
    page.on("requestfailed", record_request_failure)
    state["responseHandler"] = record_response
    state["requestFailedHandler"] = record_request_failure
    return state


def _disarm_image_upload_tracker(page: Any, tracker: dict[str, Any]) -> None:
    for event, key in (
        ("response", "responseHandler"),
        ("requestfailed", "requestFailedHandler"),
    ):
        try:
            page.remove_listener(event, tracker[key])
        except Exception:
            pass


def _wait_for_image_uploads(
    page: Any,
    tracker: dict[str, Any],
    *,
    expected_count: int,
    timeout_seconds: float,
    settle_seconds: float = IMAGE_UPLOAD_SETTLE_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    network_ready_since: float | None = None
    editor_ready_since: float | None = None
    last_snapshot: dict[str, Any] = {}
    try:
        while time.monotonic() < deadline:
            page.text_content("body")
            failures = tracker.get("failures") or []
            if failures:
                raise RuntimeError(f"图片上传失败：{failures[-1]}")
            last_snapshot = _image_upload_dom_snapshot(page)
            dom_errors = last_snapshot.get("errorCodes") or []
            if dom_errors:
                raise RuntimeError(
                    f"图片上传失败：{_diagnostic_text(dom_errors[-1])}"
                )
            completed = len(tracker.get("successfulUrls") or ())
            if completed >= expected_count:
                network_ready_since = network_ready_since or time.monotonic()
                if time.monotonic() - network_ready_since >= settle_seconds:
                    logger.info(
                        "Xiaohongshu image uploads completed",
                        extra={"expected": expected_count, "completed": completed},
                    )
                    return
            else:
                network_ready_since = None
            editor_ready = (
                bool(last_snapshot.get("titleVisible"))
                and bool(last_snapshot.get("contentVisible"))
                and int(last_snapshot.get("previewCount") or 0) >= expected_count
                and not bool(last_snapshot.get("loadingVisible"))
                and "upload_in_progress"
                not in (last_snapshot.get("statusCodes") or [])
            )
            if editor_ready:
                editor_ready_since = editor_ready_since or time.monotonic()
                if time.monotonic() - editor_ready_since >= settle_seconds:
                    _log_stage(
                        "image_upload_dom_complete",
                        "图文编辑器已稳定就绪，确认照片已被页面接收",
                        expected=expected_count,
                        network_completed=completed,
                        preview_count=int(last_snapshot.get("previewCount") or 0),
                    )
                    return
            else:
                editor_ready_since = None
            time.sleep(0.25)
    finally:
        _disarm_image_upload_tracker(page, tracker)
    completed = len(tracker.get("successfulUrls") or ())
    raise RuntimeError(
        f"等待图片上传完成超过 {int(timeout_seconds)} 秒"
        f"（网络确认 {completed}/{expected_count}）；"
        f"当前页面：{_diagnostic_url(str(getattr(page, 'url', '') or ''))}；"
        f"上传请求：{tracker.get('observed', [])[-IMAGE_UPLOAD_DIAGNOSTIC_LIMIT:]}；"
        f"页面状态：{_safe_upload_dom_snapshot(last_snapshot)}"
    )


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


def _save_verification_screenshot(page: Any, admin_id: int) -> bool:
    path = verification_image_path(admin_id)
    temporary_path = path.with_suffix(".tmp.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o750)
    challenge_markers = (
        "scan to verify",
        "scan with logged-in",
        "qr code expires",
        "账号安全",
        "扫码验证",
    )
    marker = _find_element(
        page,
        SECURITY_VERIFICATION_TEXT_SELECTORS,
        visible=True,
    )
    if marker is not None:
        handle = None
        try:
            handle = marker.evaluate_handle(
                """node => {
                    for (let current = node; current && current !== document.body;
                            current = current.parentElement) {
                        const rect = current.getBoundingClientRect();
                        if (rect.width < 180 || rect.height < 180
                                || rect.width > 1000 || rect.height > 1000) continue;
                        const hasVisibleQrMedia = [...current.querySelectorAll(
                            'img, canvas, svg, [class*="qr" i]'
                        )].some(media => {
                            const mediaRect = media.getBoundingClientRect();
                            const style = getComputedStyle(media);
                            return style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && mediaRect.width >= 100
                                && mediaRect.height >= 100
                                && mediaRect.width <= 600
                                && mediaRect.height <= 600;
                        });
                        if (hasVisibleQrMedia) return current;
                    }
                    return null;
                }"""
            )
            capture_target = handle.as_element()
            if capture_target is not None:
                capture_target.screenshot(path=str(temporary_path))
                temporary_path.chmod(0o640)
                temporary_path.replace(path)
                return True
        except Exception as exc:
            logger.warning(
                "Unable to crop Xiaohongshu verification panel: %s",
                exc,
            )
        finally:
            if handle is not None:
                try:
                    handle.dispose()
                except Exception:
                    pass
    for root in _roots(page):
        for selector in (
            '[role="dialog"]',
            '[class*="captcha"]',
            '[class*="verify"]',
            '[class*="modal"]',
        ):
            try:
                elements = root.query_selector_all(selector)
            except Exception:
                continue
            for element in elements:
                try:
                    text = (element.inner_text() or "").lower()
                    if _is_visible(element) and any(
                        marker in text for marker in challenge_markers
                    ):
                        element.screenshot(path=str(temporary_path))
                        temporary_path.chmod(0o640)
                        temporary_path.replace(path)
                        return True
                except Exception:
                    continue
    try:
        page.screenshot(path=str(temporary_path), full_page=False)
        temporary_path.chmod(0o640)
        temporary_path.replace(path)
        return True
    except Exception as exc:
        logger.warning("Unable to capture Xiaohongshu verification QR code: %s", exc)
        temporary_path.unlink(missing_ok=True)
        return False


def _security_verification_visible(page: Any) -> bool:
    return (
        _find_element(
            page,
            SECURITY_VERIFICATION_TEXT_SELECTORS,
            visible=True,
        )
        is not None
    )


def _arm_publish_diagnostics(page: Any, element: Any) -> dict[str, Any]:
    network: list[dict[str, str | int]] = []
    console: list[dict[str, str]] = []
    diagnostics_state: dict[str, Any] = {
        "securityRequired": False,
        "publishResponseStatus": None,
    }

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
            if "/web_api/sns/v2/note" in str(response.url):
                diagnostics_state["publishResponseStatus"] = int(response.status)
                if int(response.status) == 461:
                    diagnostics_state["securityRequired"] = True
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
        "state": diagnostics_state,
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
        "securityRequired": diagnostics.get("state", {}).get(
            "securityRequired", False
        ),
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
    admin_id = cli_admin_id()
    if admin_id is not None:
        clear_verification_image(admin_id)
    for path in image_paths:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Image not found: {path}")

    page = client._page
    _log_stage(
        "browser_ready",
        "虚拟浏览器启动成功",
        browser="Camoufox/Firefox",
    )
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
    _log_stage(
        "page_ready",
        "小红书图文发布页面进入成功",
        url=_diagnostic_url(page.url or PUBLISH_URL),
    )

    image_input = _wait_for_image_input(page, timeout_seconds=15)
    if image_input is None:
        raise RuntimeError("找不到图文图片上传控件，页面结构可能已更新")
    _log_stage(
        "element_ready",
        "图片上传元素捕获成功",
        element="image_input",
    )
    upload_tracker = _arm_image_upload_tracker(page)
    try:
        image_input.set_input_files(image_paths)
        _log_stage(
            "image_upload_started",
            "照片已提交至页面，等待上传完成",
            image_count=len(image_paths),
        )
        _wait_for_image_uploads(
            page,
            upload_tracker,
            expected_count=len(image_paths),
            timeout_seconds=IMAGE_UPLOAD_TIMEOUT_SECONDS,
        )
    except Exception:
        _disarm_image_upload_tracker(page, upload_tracker)
        raise
    _log_stage(
        "image_upload_complete",
        "照片上传成功",
        image_count=len(image_paths),
    )

    title_input = _wait_for_element(
        page,
        TITLE_SELECTORS,
        visible=True,
        timeout_seconds=60,
    )
    if title_input is None:
        raise RuntimeError("图片上传后找不到标题输入框，请检查上传结果或页面结构")
    _log_stage(
        "element_ready",
        "标题输入元素捕获成功",
        element="title_input",
    )
    title_input.fill(title)
    _log_stage(
        "title_filled",
        "加入标题成功",
        character_count=len(title),
    )

    if content:
        content_input = _wait_for_element(
            page,
            CONTENT_SELECTORS,
            visible=True,
            timeout_seconds=15,
        )
        if content_input is None:
            raise RuntimeError("找不到正文输入框，页面结构可能已更新")
        _log_stage(
            "element_ready",
            "正文输入元素捕获成功",
            element="content_input",
        )
        content_input.fill(content)
        _log_stage(
            "content_filled",
            "加入正文成功",
            character_count=len(content),
        )

    time.sleep(1)
    _click_element(title_input, "发布前重新聚焦标题输入框")

    publish_button = _wait_for_publish_button(page, timeout_seconds=15)
    if publish_button is None:
        raise RuntimeError("发布按钮不可点击，请检查标题、正文和图片是否通过页面校验")
    _log_stage(
        "element_ready",
        "发布按钮元素捕获成功",
        element="publish_button",
    )
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
    verification_captured = False
    verification_required_logged = False
    verification_missing_since: float | None = None
    verification_retries = 0
    publishing_without_verification_logged = False
    while time.monotonic() < deadline:
        current_url = page.url or ""
        page_text = page.text_content("body") or ""
        publish_response_status = diagnostics.get("state", {}).get(
            "publishResponseStatus"
        )
        if (
            publish_response_status is not None
            and 200 <= publish_response_status < 300
            and verification_retries == 0
            and not publishing_without_verification_logged
        ):
            _log_stage(
                "publishing",
                "无鉴权二维码，直接发布中",
                response_status=publish_response_status,
            )
            publishing_without_verification_logged = True
        note_id = (
            client._extract_note_id_from_url(current_url)
            or client._extract_note_id_from_page()
        )
        if client._is_publish_success(page_text, current_url, note_id):
            _publish_diagnostics_snapshot(page, publish_button, diagnostics)
            if admin_id is not None:
                clear_verification_image(admin_id)
            _log_stage(
                "publish_success",
                "发布笔记成功",
                note_id=note_id,
                url=_diagnostic_url(current_url),
            )
            result = {"success": True, "note_id": note_id, "url": current_url}
            return result if return_detail else True
        security_required = bool(
            diagnostics.get("state", {}).get("securityRequired")
        ) or any(
            marker in page_text.lower()
            for marker in ("scan to verify", "scan with logged-in", "qr code expires")
        )
        verification_visible = security_required and _security_verification_visible(page)
        if verification_visible and not verification_required_logged:
            _log_stage(
                "verification_required",
                "弹出发布笔记鉴权二维码，等待用户扫码",
            )
            verification_required_logged = True
        if verification_visible and not verification_captured and admin_id is not None:
            verification_captured = _save_verification_screenshot(page, admin_id)
            if verification_captured:
                deadline = max(
                    deadline,
                    time.monotonic() + SECURITY_VERIFICATION_TIMEOUT_SECONDS,
                )
                logger.warning(
                    "Xiaohongshu account security verification required; "
                    "QR screenshot is ready",
                    extra={"admin_id": admin_id},
                )
        if verification_captured and not verification_visible:
            verification_missing_since = verification_missing_since or time.monotonic()
            if (
                time.monotonic() - verification_missing_since
                >= SECURITY_VERIFICATION_SETTLE_SECONDS
                and verification_retries < 2
            ):
                clear_verification_image(admin_id)
                diagnostics["state"]["securityRequired"] = False
                diagnostics["state"]["publishResponseStatus"] = None
                verification_captured = False
                verification_required_logged = False
                verification_missing_since = None
                verification_retries += 1
                _log_stage(
                    "verification_passed",
                    "二维码鉴权通过，直接发布中",
                    attempt=verification_retries,
                )
                _click_publish(page, publish_button)
                deadline = max(deadline, time.monotonic() + PUBLISH_RESULT_TIMEOUT_SECONDS)
                logger.warning(
                    "Xiaohongshu security verification cleared; publish retried",
                    extra={"attempt": verification_retries},
                )
        elif verification_visible:
            verification_missing_since = None
        feedback = _publish_page_feedback(page)
        if feedback:
            last_feedback = feedback
        time.sleep(0.5)
    current_url = page.url or ""
    diagnostic_snapshot = _publish_diagnostics_snapshot(
        page, publish_button, diagnostics
    )
    logger.warning("Xiaohongshu publish diagnostics: %s", diagnostic_snapshot)
    if diagnostic_snapshot.get("securityRequired") or verification_captured:
        raise RuntimeError(
            "小红书返回 HTTP 461，要求账号安全扫码验证；"
            "请在发布期间使用已登录的小红书 App 扫描页面二维码后等待发布完成"
        )
    detail = f"；页面提示：{last_feedback}" if last_feedback else ""
    raise RuntimeError(
        "等待小红书发布结果超时"
        f"；当前页面：{current_url}{detail}"
    )


def main() -> None:
    from xhs_cli.cli import cli
    from xhs_cli.client import XhsClient

    XhsClient.publish_note = publish_note_compat
    cli()


if __name__ == "__main__":
    main()
