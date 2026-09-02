import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.xiaohongshu import XiaohongshuPublishJobCreate
from app.services.xiaohongshu_mcp import XiaohongshuMCPClient


@pytest.mark.asyncio
async def test_mcp_client_initializes_session_and_checks_login() -> None:
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        methods.append(payload["method"])
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "session-1"},
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}},
            )
        if payload["method"] == "notifications/initialized":
            assert request.headers["mcp-session-id"] == "session-1"
            return httpx.Response(202)
        assert request.headers["authorization"] == "Bearer bridge-secret"
        assert payload["params"]["name"] == "check_login_status"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [{"type": "text", "text": "已登录，小红书账号可用"}],
                    "isError": False,
                },
            },
        )

    client = XiaohongshuMCPClient(
        "http://127.0.0.1:18060/mcp",
        "bridge-secret",
        transport=httpx.MockTransport(handler),
    )
    logged_in, message = await client.check_login()

    assert logged_in is True
    assert "已登录" in message
    assert methods == ["initialize", "notifications/initialized", "tools/call"]


@pytest.mark.asyncio
async def test_mcp_qr_image_is_forwarded_without_cookie_data() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": "s"},
                json={"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}},
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {
                    "content": [
                        {"type": "image", "data": "cWItYnl0ZXM=", "mimeType": "image/png"}
                    ]
                },
            },
        )

    image, mime_type, _ = await XiaohongshuMCPClient(
        "http://127.0.0.1:18060/mcp", transport=httpx.MockTransport(handler)
    ).get_login_qr()

    assert image == "cWItYnl0ZXM="
    assert mime_type == "image/png"


def test_publish_job_validates_platform_limits_and_media() -> None:
    value = XiaohongshuPublishJobCreate(
        title="一篇合规标题",
        content="原创正文",
        images=["https://example.com/cover.jpg", "/srv/xhs/page-2.png"],
        tags=["#AI工具", "AI工具"],
        strategy="delayed",
        scheduled_at=datetime.now(UTC) + timedelta(hours=2),
    )
    assert value.tags == ["AI工具"]
    assert len(value.images) == 2

    with pytest.raises(ValidationError):
        XiaohongshuPublishJobCreate(
            title="超过二十个汉字的标题一定应该在进入发布队列之前被拒绝",
            content="正文",
            images=["relative/image.png"],
        )
