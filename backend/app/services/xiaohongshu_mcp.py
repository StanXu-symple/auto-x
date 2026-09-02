from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx


class XiaohongshuMCPError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class MCPToolResult:
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None
    raw: dict[str, Any]

    @property
    def text(self) -> str:
        return "\n".join(
            str(item.get("text") or "")
            for item in self.content
            if item.get("type") == "text"
        ).strip()


class XiaohongshuMCPClient:
    def __init__(
        self,
        url: str,
        auth_token: str | None = None,
        *,
        timeout_seconds: float = 180,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.url = url
        self.auth_token = auth_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self._request_id = 0

    def _headers(self, session_id: str | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return headers

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            payloads = []
            for line in response.text.splitlines():
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    if raw and raw != "[DONE]":
                        payloads.append(json.loads(raw))
            if not payloads:
                raise XiaohongshuMCPError("小红书 MCP 返回了空的事件流")
            return payloads[-1]
        try:
            return response.json()
        except ValueError as exc:
            raise XiaohongshuMCPError("小红书 MCP 没有返回有效 JSON") from exc

    async def _post(
        self,
        client: httpx.AsyncClient,
        method: str,
        params: dict[str, Any] | None,
        *,
        session_id: str | None = None,
        notification: bool = False,
    ) -> tuple[dict[str, Any], str | None]:
        self._request_id += 1
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notification:
            payload["id"] = self._request_id
        try:
            response = await client.post(
                self.url,
                json=payload,
                headers=self._headers(session_id),
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise XiaohongshuMCPError(f"无法连接小红书 MCP：{exc}") from exc
        if response.status_code in {401, 403}:
            raise XiaohongshuMCPError("小红书 MCP 访问令牌无效", retryable=False)
        if response.is_error:
            raise XiaohongshuMCPError(f"小红书 MCP 返回 HTTP {response.status_code}")
        returned_session = response.headers.get("mcp-session-id") or session_id
        if notification and not response.content:
            return {}, returned_session
        body = self._decode_response(response)
        if body.get("error"):
            error = body["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise XiaohongshuMCPError(f"MCP 协议错误：{message}")
        return body, returned_session

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            initialized, session_id = await self._post(
                client,
                "initialize",
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "x-sentinel", "version": "1.0.0"},
                },
            )
            if not initialized.get("result"):
                raise XiaohongshuMCPError("小红书 MCP 初始化失败")
            await self._post(
                client,
                "notifications/initialized",
                None,
                session_id=session_id,
                notification=True,
            )
            body, _ = await self._post(
                client,
                "tools/call",
                {"name": name, "arguments": arguments or {}},
                session_id=session_id,
            )
        result = body.get("result") or {}
        content = result.get("content") or []
        if result.get("isError"):
            message = "\n".join(
                str(item.get("text") or "") for item in content if isinstance(item, dict)
            ).strip()
            retryable = not any(
                word in message.lower() for word in ("未登录", "not logged", "参数")
            )
            raise XiaohongshuMCPError(message or f"工具 {name} 执行失败", retryable=retryable)
        return MCPToolResult(
            content=[item for item in content if isinstance(item, dict)],
            structured_content=result.get("structuredContent"),
            raw=result,
        )

    async def check_login(self) -> tuple[bool, str]:
        result = await self.call_tool("check_login_status")
        text = result.text
        normalized = text.lower()
        logged_in = any(
            marker in normalized
            for marker in ("已登录", "登录成功", "logged in", '"logged_in":true')
        ) and not any(marker in normalized for marker in ("未登录", "not logged"))
        return logged_in, text or ("已登录" if logged_in else "未检测到登录状态")

    async def get_login_qr(self) -> tuple[str | None, str, str]:
        result = await self.call_tool("get_login_qrcode")
        for item in result.content:
            if item.get("type") == "image" and item.get("data"):
                return str(item["data"]), str(item.get("mimeType") or "image/png"), result.text
        candidates: list[Any] = [result.structured_content, result.text]
        for candidate in candidates:
            if isinstance(candidate, str):
                try:
                    candidate = json.loads(candidate)
                except ValueError:
                    match = re.search(r"base64[,=: ]+([A-Za-z0-9+/=]{100,})", candidate)
                    if match:
                        return match.group(1), "image/png", result.text
            if isinstance(candidate, dict):
                for key in ("image", "image_data", "qr_code", "qrcode", "base64"):
                    value = candidate.get(key)
                    if isinstance(value, str) and value:
                        if value.startswith("data:") and "," in value:
                            header, value = value.split(",", 1)
                            mime = header[5:].split(";")[0] or "image/png"
                            return value, mime, result.text
                        return value, "image/png", result.text
        return None, "image/png", result.text or "MCP 未返回二维码图片"

    async def publish_content(self, arguments: dict[str, Any]) -> MCPToolResult:
        return await self.call_tool("publish_content", arguments)
