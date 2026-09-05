from app.api.routes.xhs import _publish_error


def test_publish_error_explains_browser_resource_failure_from_stdout() -> None:
    message = _publish_error(
        "Publish failed: Page.goto: Target page, context or browser has been closed",
        "non-fatal browser log",
    )

    assert "浏览器意外退出" in message
    assert "1.5GB" in message
    assert "512MB /dev/shm" in message
    assert "Target page, context or browser has been closed" in message


def test_publish_error_preserves_other_cli_errors() -> None:
    assert _publish_error("", "creator login required") == "creator login required"
