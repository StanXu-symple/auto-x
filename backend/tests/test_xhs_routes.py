import pytest
from pydantic import ValidationError

from app.api.routes.xhs import PostPayload
from app.services.xhs_jobs import publish_error
from app.services.xhs_limits import (
    XHS_NOTE_CONTENT_MAX_LENGTH,
    XHS_NOTE_TITLE_MAX_LENGTH,
)


def test_xhs_post_payload_enforces_platform_text_limits() -> None:
    payload = PostPayload(
        title="标" * XHS_NOTE_TITLE_MAX_LENGTH,
        content="文" * XHS_NOTE_CONTENT_MAX_LENGTH,
        images=["/tmp/image.png"],
    )
    assert len(payload.title) == XHS_NOTE_TITLE_MAX_LENGTH
    assert len(payload.content) == XHS_NOTE_CONTENT_MAX_LENGTH

    with pytest.raises(ValidationError):
        PostPayload(
            title="标" * (XHS_NOTE_TITLE_MAX_LENGTH + 1),
            content="正文",
            images=["/tmp/image.png"],
        )
    with pytest.raises(ValidationError):
        PostPayload(
            title="标题",
            content="文" * (XHS_NOTE_CONTENT_MAX_LENGTH + 1),
            images=["/tmp/image.png"],
        )


def test_publish_error_explains_browser_resource_failure_from_stdout() -> None:
    message = publish_error(
        "Publish failed: Page.goto: Target page, context or browser has been closed",
        "non-fatal browser log",
    )

    assert "浏览器意外退出" in message
    assert "xhs-worker" in message
    assert "Target page, context or browser has been closed" in message


def test_publish_error_preserves_other_cli_errors() -> None:
    assert publish_error("", "creator login required") == "creator login required"


def test_publish_error_combines_stdout_and_stderr() -> None:
    message = publish_error(
        '{"success": false}',
        "Title input not found\nContent input not found",
    )

    assert '{"success": false}' in message
    assert "Title input not found" in message


def test_publish_error_explains_page_crash() -> None:
    message = publish_error(
        "Publishing note",
        "Publish failed: Page.goto: Page crashed",
    )

    assert "页面崩溃" in message
    assert "2GB" in message
    assert "cgroup_oom_kill_delta" in message


def test_publish_error_reports_confirmed_cgroup_oom() -> None:
    message = publish_error(
        "",
        "Page crashed\nXHS_WORKER_CGROUP_OOM: oom_kill increased by 1",
    )

    assert "已确认" in message
    assert "内存上限" in message
