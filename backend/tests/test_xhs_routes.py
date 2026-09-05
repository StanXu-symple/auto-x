from app.services.xhs_jobs import publish_error


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
