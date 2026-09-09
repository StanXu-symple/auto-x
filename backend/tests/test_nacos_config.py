from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from app.control_plane.nacos import NacosClient, NacosConfigClient, NacosConfigError, merge_config


def test_merge_config_is_recursive_and_does_not_mutate_inputs() -> None:
    base = {"postgres": {"host": "postgres", "port": 5432}, "features": ["old"]}
    override = {"postgres": {"host": "10.0.0.2"}, "features": ["new"]}

    merged = merge_config(base, override)

    assert merged == {
        "postgres": {"host": "10.0.0.2", "port": 5432},
        "features": ["new"],
    }
    assert base["postgres"]["host"] == "postgres"
    assert base["features"] == ["old"]


def test_sync_client_get_publish_and_token_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/nacos/v1/auth/login":
            return httpx.Response(200, json={"accessToken": "token-1", "tokenTtl": 1800})
        if request.method == "GET":
            assert request.url.params["dataId"] == "x-sentinel-config.json"
            assert request.url.params["group"] == "X_SENTINEL"
            assert request.url.params["tenant"] == "tenant-a"
            assert request.url.params["accessToken"] == "token-1"
            return httpx.Response(200, text='{"redis":{"host":"redis"}}')
        assert request.method == "POST"
        values = parse_qs(request.content.decode())
        assert values["dataId"] == ["x-sentinel-config.json"]
        assert values["group"] == ["X_SENTINEL"]
        assert values["tenant"] == ["tenant-a"]
        assert values["type"] == ["json"]
        assert '"host": "10.0.0.4"' in values["content"][0]
        assert values["accessToken"] == ["token-1"]
        return httpx.Response(200, text="true")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        client = NacosConfigClient(
            "http://nacos:8848",
            namespace="tenant-a",
            username="nacos",
            password="secret",
            http=http,
        )
        assert client.get_json_config("x-sentinel-config.json") == {"redis": {"host": "redis"}}
        assert client.publish_json_config("x-sentinel-config.json", {"redis": {"host": "10.0.0.4"}})

    assert [request.url.path for request in requests] == [
        "/nacos/v1/auth/login",
        "/nacos/v1/cs/configs",
        "/nacos/v1/cs/configs",
    ]


def test_client_accepts_nacos_context_suffix() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        client = NacosConfigClient("http://nacos:8848/nacos/", http=http)
        assert client.server == "http://nacos:8848"


def test_sync_client_from_env_prefers_config_group() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        client = NacosConfigClient.from_env(
            {
                "NACOS_SERVER_ADDR": "http://nacos:8848",
                "NACOS_GROUP": "NAMING_GROUP",
                "NACOS_CONFIG_GROUP": "CONFIG_GROUP",
            },
            http=http,
        )
        assert client.group == "CONFIG_GROUP"


def test_sync_client_treats_missing_or_empty_config_as_default() -> None:
    responses = iter([httpx.Response(404), httpx.Response(200, text=" ")])

    def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = NacosConfigClient("http://nacos:8848", http=http)
        assert client.get_json_config("missing", default={"app": {"debug": False}}) == {
            "app": {"debug": False}
        }
        assert client.get_json_config("empty") is None


def test_sync_client_merge_remote_config_and_publish_patch() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"postgres": {"host": "db", "port": 5432}})
        return httpx.Response(200, text="true")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = NacosConfigClient("http://nacos:8848", http=http)
        assert client.merge_remote_config(
            "config", {"postgres": {"host": "localhost", "user": "root"}}
        ) == {"postgres": {"host": "db", "port": 5432, "user": "root"}}
        assert client.merge_json_config("config", {"redis": {"db": 1}}) == {
            "postgres": {"host": "db", "port": 5432},
            "redis": {"db": 1},
        }
    assert calls == ["GET", "GET", "POST"]


def test_sync_client_rejects_invalid_json() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="[]")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = NacosConfigClient("http://nacos:8848", http=http)
        with pytest.raises(NacosConfigError, match="JSON object"):
            client.get_json_config("config")


@pytest.mark.asyncio
async def test_async_client_and_existing_naming_client_share_auth() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/nacos/v1/auth/login":
            return httpx.Response(200, json={"accessToken": "token-2", "tokenTtl": 1800})
        assert request.url.params["accessToken"] == "token-2"
        return httpx.Response(200, text='{"app":{"debug":true}}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        naming = NacosClient(
            http, "http://nacos:8848/nacos/", "public", "X_SENTINEL", "nacos", "pw"
        )
        config = naming.config
        assert await config.get_json_config("config") == {"app": {"debug": True}}
        assert await naming.config.get_json_config("config") == {"app": {"debug": True}}

    assert [request.url.path for request in requests] == [
        "/nacos/v1/auth/login",
        "/nacos/v1/cs/configs",
        "/nacos/v1/cs/configs",
    ]
