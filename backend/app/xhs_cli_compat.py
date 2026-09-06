from __future__ import annotations

import logging
import os
import struct
import time
import zlib
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
                _click_element(tab, "点击上传图文页签")
                logger.info("Selected Xiaohongshu image-text publish tab")
                return
        time.sleep(0.3)
    raise RuntimeError("找不到小红书创作中心的“上传图文”页签，页面结构可能已更新")


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


def _paeth_predictor(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    left_distance = abs(estimate - left)
    above_distance = abs(estimate - above)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= above_distance and left_distance <= upper_left_distance:
        return left
    if above_distance <= upper_left_distance:
        return above
    return upper_left


def _decode_png(png: bytes) -> tuple[int, int, int, bytes]:
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("截图不是 PNG 格式")
    width = height = channels = 0
    compressed = bytearray()
    offset = 8
    while offset + 12 <= len(png):
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        chunk_type = png[offset + 4 : offset + 8]
        chunk_data = png[offset + 8 : offset + 8 + length]
        offset += length + 12
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = (
                struct.unpack(">IIBBBBB", chunk_data)
            )
            if bit_depth != 8 or interlace != 0 or color_type not in {2, 6}:
                raise ValueError(
                    f"不支持的 PNG 格式：bit_depth={bit_depth} "
                    f"color_type={color_type} interlace={interlace}"
                )
            channels = 3 if color_type == 2 else 4
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if not width or not height or not channels or not compressed:
        raise ValueError("PNG 截图数据不完整")

    encoded = zlib.decompress(bytes(compressed))
    stride = width * channels
    expected_size = height * (stride + 1)
    if len(encoded) != expected_size:
        raise ValueError(f"PNG 像素长度异常：expected={expected_size} actual={len(encoded)}")

    decoded = bytearray(height * stride)
    previous = bytearray(stride)
    source_offset = 0
    for row_index in range(height):
        filter_type = encoded[source_offset]
        source_offset += 1
        source = encoded[source_offset : source_offset + stride]
        source_offset += stride
        row = bytearray(stride)
        for index, value in enumerate(source):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                predictor = _paeth_predictor(left, above, upper_left)
            else:
                raise ValueError(f"不支持的 PNG filter type：{filter_type}")
            row[index] = (value + predictor) & 0xFF
        start = row_index * stride
        decoded[start : start + stride] = row
        previous = row
    return width, height, channels, bytes(decoded)


def _find_red_button_position(png: bytes) -> dict[str, float]:
    width, height, channels, pixels = _decode_png(png)
    red_pixels: set[tuple[int, int]] = set()
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * channels
            red, green, blue = pixels[offset : offset + 3]
            alpha = pixels[offset + 3] if channels == 4 else 255
            if alpha >= 192 and red >= 180 and red - green >= 55 and red - blue >= 35:
                red_pixels.add((x, y))

    largest: list[tuple[int, int]] = []
    remaining = set(red_pixels)
    while remaining:
        start = remaining.pop()
        component = [start]
        stack = [start]
        while stack:
            x, y = stack.pop()
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.append(neighbor)
                    stack.append(neighbor)
        if len(component) > len(largest):
            largest = component

    minimum_area = max(100, round(width * height * 0.005))
    if len(largest) < minimum_area:
        raise ValueError(
            f"未识别到红色发布按钮：size={width}x{height} "
            f"largest_red_area={len(largest)} minimum={minimum_area}"
        )
    xs = [point[0] for point in largest]
    ys = [point[1] for point in largest]
    return {"x": (min(xs) + max(xs)) / 2, "y": (min(ys) + max(ys)) / 2}


def _click_publish(page: Any, element: Any) -> None:
    try:
        tag_name = str(element.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:
        tag_name = ""
    if tag_name == "xhs-publish-btn":
        errors: list[str] = []
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

        box = element.bounding_box()
        position: dict[str, float] | None = None
        if not box or box["width"] <= 0 or box["height"] <= 0:
            errors.append(f"无有效点击区域：{box}")
        else:
            try:
                screenshot = element.screenshot(type="png", timeout=10000)
                screenshot_width, screenshot_height = struct.unpack(">II", screenshot[16:24])
                pixel_position = _find_red_button_position(screenshot)
                position = {
                    "x": pixel_position["x"] * box["width"] / screenshot_width,
                    "y": pixel_position["y"] * box["height"] / screenshot_height,
                }
            except Exception as exc:
                errors.append(f"red button detection: {exc}")

        if box and position:
            try:
                element.click(timeout=5000, force=True, position=position)
            except Exception as exc:
                errors.append(f"red button click: {exc}")
            else:
                logger.warning(
                    "Clicked Xiaohongshu red publish button with a real mouse event: "
                    "box=%s position=%s",
                    box,
                    position,
                )
                return

        raise RuntimeError(f"点击小红书发布按钮失败：{' | '.join(errors)}")
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
