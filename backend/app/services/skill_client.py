"""Nacos + HTTP only: failed discovery/validation must never become an empty selection."""

import asyncio
from contextlib import asynccontextmanager

import httpx
from pydantic import TypeAdapter

from app.api.errors import APIError
from app.control_plane.nacos import NacosClient
from app.control_plane.security import TokenClient
from app.core.config import Settings
from app.schemas.ai import AISkillOut
from app.schemas.skill_service import SkillResolution, TaskSkillSelection


class SkillServiceUnavailable(APIError):
    def __init__(self) -> None:
        super().__init__(503, "skill_service_unavailable", "Skill 服务暂不可用，请稍后重试")


class SkillClient:
    def __init__(self, settings: Settings, *, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.http = http or httpx.AsyncClient(
            timeout=settings.skill_request_timeout_seconds,
            trust_env=False,
        )
        self._owns_http = http is None
        self.nacos = (
            NacosClient(
                self.http,
                settings.nacos_server_addr,
                settings.nacos_namespace,
                settings.nacos_group,
                settings.nacos_username,
                settings.nacos_password,
            )
            if settings.nacos_server_addr
            else None
        )
        self.tokens: TokenClient | None = None

    async def aclose(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            # Includes discovery and token acquisition, bounding caller DB lock time.
            async with asyncio.timeout(self.settings.skill_request_timeout_seconds):
                if self.nacos is not None:
                    base = (await self.nacos.discover(self.settings.skill_service_name)).url
                else:
                    base = self.settings.skill_service_url.rstrip("/")
                if not base:
                    raise RuntimeError("Skill service is not configured")
                if self.tokens is None:
                    self.tokens = TokenClient(
                        self.http,
                        self.settings.service_auth_url,
                        self.settings.skill_client_id,
                        self.settings.skill_client_secret_file,
                        nacos=self.nacos,
                    )
                headers = dict(kwargs.pop("headers", {}))
                headers["Authorization"] = f"Bearer {await self.tokens.token('skill-worker')}"
                # Do not retry mutations: a timeout can occur after the remote commit.
                response = await self.http.request(
                    method,
                    f"{base}{path}",
                    headers=headers,
                    **kwargs,
                )
                if response.status_code >= 500:
                    raise SkillServiceUnavailable()
                # Service credential failures are an unavailable dependency, not
                # an expired administrator session in the browser.
                if response.status_code in {401, 403}:
                    body = response.json()
                    error = body.get("error", {}) if isinstance(body, dict) else {}
                    if error.get("code") == "http_error":
                        raise SkillServiceUnavailable()
                return response
        except (
            httpx.HTTPError,
            TimeoutError,
            OSError,
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ) as exc:
            raise SkillServiceUnavailable() from exc

    async def resolve(self, skill_ids: list[int]) -> list[AISkillOut]:
        response = await self.request("POST", "/v1/skills/resolve", json={"skill_ids": skill_ids})
        try:
            response.raise_for_status()
            skills = TypeAdapter(list[AISkillOut]).validate_python(response.json())
            by_id = {s.id: s for s in skills}
            if any(not s.is_active for s in skills) or [s.id for s in skills] != [
                i for i in skill_ids if i in by_id
            ]:
                raise ValueError("Unexpected skill ordering")
            return skills
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise SkillServiceUnavailable() from exc

    async def context(self, **payload) -> SkillResolution:
        response = await self.request("POST", "/v1/skills/context", json=payload)
        try:
            response.raise_for_status()
            result = SkillResolution.model_validate(response.json())
            override = payload.get("override_skill_ids")
            if override is not None and result.source != "manual_override":
                raise ValueError("Unexpected override source")
            if override is None and result.source == "manual_override":
                raise ValueError("Unexpected override source")
            expected_ids = override if override is not None else payload["fallback_skill_ids"]
            ids = [s.id for s in result.skills]
            if any(not s.is_active for s in result.skills) or (
                result.source != "user_feature_binding"
                and ids != [i for i in expected_ids if i in set(ids)]
            ):
                raise ValueError("Unexpected context selection")
            return result
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise SkillServiceUnavailable() from exc

    async def task(self, task_id: int) -> TaskSkillSelection:
        response = await self.request("GET", f"/v1/tasks/{task_id}/skills")
        try:
            response.raise_for_status()
            selection = TaskSkillSelection.model_validate(response.json())
            if selection.task_id != task_id:
                raise ValueError("Unexpected task")
            return selection
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise SkillServiceUnavailable() from exc


# One client per process, installed by its lifecycle. Tests can inject an HTTP-backed
# client here; application code has no local-store fallback.
CLIENT: SkillClient | None = None


def get_skill_client() -> SkillClient:
    if CLIENT is None:
        raise SkillServiceUnavailable()
    return CLIENT


@asynccontextmanager
async def skill_client_lifespan(settings: Settings):
    global CLIENT
    client = SkillClient(settings)
    previous, CLIENT = CLIENT, client
    try:
        yield client
    finally:
        CLIENT = previous
        await client.aclose()


async def resolve_active_skills(_session, skill_ids: list[int]) -> list[AISkillOut]:
    return await get_skill_client().resolve(skill_ids)


async def resolve_context_skills(
    _session,
    *,
    monitored_user_id,
    feature,
    fallback_skill_ids,
    override_skill_ids=None,
) -> tuple[list[AISkillOut], str]:
    result = await get_skill_client().context(
        monitored_user_id=monitored_user_id,
        feature_id=feature.id,
        fallback_skill_ids=fallback_skill_ids,
        override_skill_ids=override_skill_ids,
    )
    return result.skills, result.source


async def resolve_task_skills(task_id: int) -> TaskSkillSelection:
    return await get_skill_client().task(task_id)
