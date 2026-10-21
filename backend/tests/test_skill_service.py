"""Exercise the real HTTP facade, JWT checks and service-owned SQL together."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from test_ai_listen_tasks import FakeRedis, database  # noqa: F401
from test_user_authentication import service_token, signing_material, user_token

from app import skill_worker
from app.api.deps import get_db
from app.control_plane.nacos import Instance
from app.control_plane.security import ServiceVerifier
from app.core.config import Settings
from app.db.base import Base
from app.main import app as backend
from app.models.ai import AIFeature, AIGenerationJob, AIListenTask
from app.models.ai_data_source import AIDataSource
from app.models.service_auth import ServiceAuthSession
from app.models.tweet import Tweet
from app.services import skill_client
from app.services.ai_jobs import create_manual_job, enqueue_listening_jobs, get_ai_setting
from app.services.auth_center import AuthCenterClient
from app.services.skill_client import SkillClient, SkillServiceUnavailable


@pytest.fixture
async def cluster(database, monkeypatch):  # noqa: F811
    private, jwk = signing_material("skill-test")
    admin_token = user_token(private, "skill-test", uid=9, sub="publisher")
    backend_token = service_token(
        private,
        "skill-test",
        sub="skill-backend",
        aud="skill-worker",
        scope="skills:read skills:manage",
    )
    reader_token = service_token(
        private, "skill-test", sub="skill-poller", aud="skill-worker", scope="skills:read"
    )
    async with database() as db:
        engine = db.bind
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync,
                    tables=[
                        Base.metadata.tables[name]
                        for name in (
                            "service_auth_sessions",
                            "service_auth_revocations",
                            "ai_user_profiles",
                            "ai_generation_attempts",
                            "ai_drafts",
                            "ai_publish_dispatches",
                        )
                    ],
                )
            )
        db.add(
            ServiceAuthSession(
                sid="12345678-1234-4234-9234-123456789abc",
                admin_id=9,
                jti="abcdefab-1234-4234-9234-123456789abc",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await db.commit()

    async def sessions():
        async with database() as db:
            yield db

    settings = Settings(
        _env_file=None,
        nacos_config_enabled=False,
        nacos_server_addr="http://nacos",
        service_auth_url="http://auth",
        skill_service_url="http://unused",
    )
    auth_http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"keys": [jwk]}))
    )
    auth = AuthCenterClient(settings, http=auth_http)
    for app in (backend, skill_worker.app):
        monkeypatch.setitem(app.dependency_overrides, get_db, sessions)
        monkeypatch.setattr(app.state, "auth_center", auth, raising=False)
        monkeypatch.setattr(app.state, "redis", FakeRedis(), raising=False)
    for scope in ("read", "manage"):
        monkeypatch.setattr(
            skill_worker.app.state,
            f"skill_{scope}_verifier",
            ServiceVerifier(private.public_key(), "skill-worker", f"skills:{scope}"),
            raising=False,
        )
    transport = httpx.ASGITransport(app=skill_worker.app)
    remote = httpx.AsyncClient(transport=transport, base_url="http://skills:8204")
    client = SkillClient(settings, http=remote)
    client.nacos.discover = AsyncMock(
        return_value=Instance("skills", 8204, "xsentinel-skill-worker")
    )
    client.tokens = SimpleNamespace(token=AsyncMock(return_value=backend_token))
    monkeypatch.setattr(skill_client, "CLIENT", client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=backend),
        base_url="http://backend",
        headers={"Authorization": f"Bearer {admin_token}"},
    ) as api:
        yield SimpleNamespace(
            api=api,
            client=client,
            remote=remote,
            factory=database,
            admin_token=admin_token,
            backend_token=backend_token,
            reader_token=reader_token,
        )
    await remote.aclose()
    await auth_http.aclose()


async def test_http_crud_and_explicit_skill_order(cluster):
    api = cluster.api
    created = await api.post("/api/v1/ai/skills", json={"name": "第二个", "instructions": "v1"})
    assert created.status_code == 201, created.text
    assert created.json()["content_type"] == "prompt"
    assert created.json()["source_type"] == "custom"
    second = created.json()["id"]
    assert [s.id for s in await cluster.client.resolve([second, 1])] == [second, 1]
    assert [s.id for s in await cluster.client.resolve([1, second])] == [1, second]
    changed = await api.patch(f"/api/v1/ai/skills/{second}", json={"instructions": "v2"})
    assert changed.json()["version"] == 2
    assert changed.json()["content_type"] == "prompt"
    assert (await api.delete(f"/api/v1/ai/skills/{second}")).status_code == 200
    result = await api.get("/api/v1/ai/skills", params={"active": "false", "page_size": 1})
    assert result.status_code == 200 and result.json()["total"] == 1
    assert result.json()["items"][0]["id"] == second
    assert [s.id for s in await cluster.client.resolve([second, 1])] == [1]
    cluster.client.nacos.discover.assert_awaited_with("xsentinel-skill-worker")


async def test_manual_crud_cannot_create_or_reclassify_repository_skills(cluster):
    api = cluster.api
    for fields in ({"content_type": "skill"}, {"source_type": "github"}):
        response = await api.post(
            "/api/v1/ai/skills", json={"name": "Invalid", "instructions": "Text", **fields}
        )
        assert response.status_code == 422, response.text
    for fields in ({"content_type": "skill"}, {"source_type": "github"}):
        response = await api.patch("/api/v1/ai/skills/1", json=fields)
        assert response.status_code == 422, response.text
    created = await api.post(
        "/api/v1/ai/skills",
        json={"name": "Explicit prompt", "instructions": "Text", "content_type": "prompt"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["content_type"] == "prompt"


async def test_account_binding_and_implicit_selection_endpoints_are_removed(cluster):
    path = "/api/v1/ai/users/1/skill-bindings/article_generation"
    assert (await cluster.api.get(path)).status_code == 404
    assert (await cluster.api.put(path, json={"skill_ids": [1]})).status_code == 404
    response = await cluster.remote.post(
        "/v1/skills/context",
        headers={"Authorization": f"Bearer {cluster.reader_token}"},
        json={"monitored_user_id": 1, "feature_id": 1, "fallback_skill_ids": [1]},
    )
    assert response.status_code == 404
    for payload in ({"default_skill_ids": [1]}, {"auto_trigger_mode": "legacy_all"}):
        assert (await cluster.api.patch("/api/v1/ai/settings", json=payload)).status_code == 422


async def test_http_task_switch_snapshot_and_deactivation_are_atomic(cluster):
    api = cluster.api
    created = await api.post(
        "/api/v1/ai/listen-tasks",
        json={
            "name": "监听",
            "desired_state": "paused",
            "monitored_user_ids": [1],
            "skill_ids": [1],
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    assert [skill["content_type"] for skill in task["skills"]] == ["prompt"]
    second = (
        await api.post(
            "/api/v1/ai/skills",
            json={
                "name": "新绑定",
                "instructions": "新指令",
            },
        )
    ).json()["id"]
    changed = await api.patch(
        f"/api/v1/ai/listen-tasks/{task['id']}",
        json={
            "config_version": task["config_version"],
            "skill_ids": [second, 1],
        },
    )
    assert changed.status_code == 200, changed.text
    selection = await cluster.client.task(task["id"])
    assert selection.valid and selection.selected_ids == [second, 1]
    stale = await api.patch(
        f"/api/v1/ai/listen-tasks/{task['id']}",
        json={
            "config_version": task["config_version"],
            "skill_ids": [1],
        },
    )
    assert stale.status_code == 409
    async with cluster.factory() as db:
        db.add(
            Tweet(
                id=11,
                tweet_id="x11",
                monitored_user_id=1,
                author_id="a",
                text="source",
                posted_at=datetime.now(UTC),
                raw_payload={},
            )
        )
        await db.commit()
        await get_ai_setting(db, for_update=True)
        tweet = await db.get(Tweet, 11)
        feature = await db.get(AIFeature, 1)
        job, _ = await create_manual_job(
            db,
            tweet=tweet,
            feature=feature,
            skills=selection.skills,
            skill_resolution="manual_override",
            idempotency_key="snapshot-test",
        )
        await db.commit()
        job_id, frozen = job.id, job.skill_snapshot
    assert (
        await api.patch(f"/api/v1/ai/skills/{second}", json={"instructions": "修改后的指令"})
    ).status_code == 200
    assert (await api.delete(f"/api/v1/ai/skills/{second}")).status_code == 200
    async with cluster.factory() as db:
        assert (await db.get(AIGenerationJob, job_id)).skill_snapshot == frozen
        assert frozen[0]["instructions"] == "新指令"
        assert (await db.get(AIListenTask, task["id"])).queue_hold_reason == "skill_invalidated"
    assert not (await cluster.client.task(task["id"])).valid


async def test_service_scopes_and_delegated_user_are_both_required(cluster):
    remote = cluster.remote
    reader = {"Authorization": f"Bearer {cluster.reader_token}"}
    assert (
        await remote.post("/v1/skills/resolve", headers=reader, json={"skill_ids": [1]})
    ).status_code == 200
    assert (
        await remote.get(
            "/ai/skills",
            headers={
                **reader,
                "X-User-Authorization": f"Bearer {cluster.admin_token}",
            },
        )
    ).status_code == 401
    manager = {"Authorization": f"Bearer {cluster.backend_token}"}
    assert (await remote.get("/ai/skills", headers=manager)).status_code == 401
    assert (
        await remote.get(
            "/ai/skills",
            headers={
                **manager,
                "X-User-Authorization": "Bearer forged",
                "X-Admin-ID": "9",
            },
        )
    ).status_code == 401
    async with cluster.factory() as db:
        session = await db.scalar(select(ServiceAuthSession))
        session.revoked_at = datetime.now(UTC)
        await db.commit()
    assert (
        await remote.get(
            "/ai/skills",
            headers={
                **manager,
                "X-User-Authorization": f"Bearer {cluster.admin_token}",
            },
        )
    ).status_code == 401


@pytest.mark.parametrize("failure", ["discovery", "timeout", "invalid", "server"])
async def test_failures_never_become_empty_skills_or_retry_mutations(cluster, failure):
    client = cluster.client
    calls = []

    async def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        if failure == "invalid":
            return httpx.Response(200, json={"skills": []})
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client.http = http
        if failure == "discovery":
            client.nacos.discover.side_effect = RuntimeError("no healthy instances")
        with pytest.raises(SkillServiceUnavailable):
            await client.resolve([1])
        assert len(calls) == (0 if failure == "discovery" else 1)
        if failure == "timeout":
            calls.clear()
            with pytest.raises(SkillServiceUnavailable):
                await client.request("POST", "/ai/skills", json={"name": "one"})
            assert len(calls) == 1


async def test_task_owner_check_survives_backend_forwarding(cluster):
    async with cluster.factory() as db:
        # An existing task owned by another administrator remains protected.
        task = AIListenTask(name="他人任务", owner_admin_id=99, desired_state="paused")
        db.add(task)
        await db.commit()
        task_id = task.id
    response = await cluster.api.post(f"/api/v1/ai/listen-tasks/{task_id}/archive")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ai_listen_task_owner_required"


async def test_http_deadline_includes_discovery(cluster):
    cluster.client.settings.skill_request_timeout_seconds = 0.02

    async def slow_discovery(_service):
        await asyncio.sleep(1)

    cluster.client.nacos.discover.side_effect = slow_discovery
    with pytest.raises(SkillServiceUnavailable):
        await cluster.client.resolve([1])


async def test_service_token_rejection_does_not_log_administrator_out(cluster):
    cluster.client.tokens.token.return_value = cluster.reader_token
    result = await cluster.api.get("/api/v1/ai/skills")
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "skill_service_unavailable"


async def test_skill_worker_registers_and_closes_resources(monkeypatch):
    from fastapi import FastAPI
    from test_init_db import FakeSession

    settings = Settings(_env_file=None, nacos_config_enabled=False, nacos_server_addr="")
    registration = SimpleNamespace(aclose=AsyncMock())
    register = AsyncMock(return_value=registration)
    monkeypatch.setattr(skill_worker, "get_settings", lambda: settings)
    monkeypatch.setattr(skill_worker, "start_service_registration", register)
    monkeypatch.setattr(skill_worker, "AsyncSessionFactory", lambda: FakeSession())
    seed = AsyncMock()
    monkeypatch.setattr(skill_worker, "seed_skills", seed)
    monkeypatch.setattr(skill_worker, "engine", SimpleNamespace(dispose=AsyncMock()))
    monkeypatch.setattr(skill_worker, "configure_logging", lambda _level: None)
    async with skill_worker.lifespan(FastAPI()):
        assert register.call_args.args[:3] == (settings, "xsentinel-skill-worker", 8204)
        seed.assert_awaited_once()
    registration.aclose.assert_awaited_once()


async def test_outage_rolls_back_enqueue_and_leaves_claim_attempts_untouched(cluster, monkeypatch):
    from app.ai_worker import AIGenerationWorker

    async with cluster.factory() as db:
        db.add(
            AIDataSource(
                id=1,
                base_url="https://example.test",
                model_name="test",
                encrypted_api_key="test",
                key_hint="test",
                key_fingerprint="test",
            )
        )
        await db.commit()
    response = await cluster.api.post(
        "/api/v1/ai/listen-tasks",
        json={
            "name": "故障恢复",
            "desired_state": "enabled",
            "monitored_user_ids": [1],
            "skill_ids": [1],
        },
    )
    assert response.status_code == 201, response.text
    task_id = response.json()["id"]
    cluster.client.nacos.discover.side_effect = RuntimeError("no healthy instances")
    # Enqueue shares the ingestion transaction: a failed remote read rolls all
    # writes back, including the newly collected tweet and its next checkpoint.
    with pytest.raises(SkillServiceUnavailable):
        async with cluster.factory() as db, db.begin():
            db.add(
                Tweet(
                    id=111,
                    tweet_id="outage",
                    monitored_user_id=1,
                    author_id="a",
                    text="source",
                    posted_at=datetime.now(UTC),
                    raw_payload={},
                )
            )
            await db.flush()
            await enqueue_listening_jobs(db, [111])
    async with cluster.factory() as db:
        assert await db.get(Tweet, 111) is None
        task = await db.get(AIListenTask, task_id)
        assert task.queue_hold_reason is None
        db.add(
            Tweet(
                id=112,
                tweet_id="existing",
                monitored_user_id=1,
                author_id="a",
                text="source",
                posted_at=datetime.now(UTC),
                raw_payload={},
            )
        )
        db.add(
            AIGenerationJob(
                id=22,
                source_tweet_id=112,
                listen_task_id=task_id,
                idempotency_key="outage-claim",
                status="queued",
                next_attempt_at=datetime.now(UTC),
                provider="openai_responses",
                model_name="test",
                attempts=0,
                max_attempts=3,
                manual=False,
                skill_ids=[1],
                skill_snapshot=[],
                request_snapshot={},
            )
        )
        await db.commit()
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", cluster.factory)
    worker = object.__new__(AIGenerationWorker)
    worker.settings = cluster.client.settings
    worker.worker_id = "test"
    with pytest.raises(SkillServiceUnavailable):
        await worker._claim(22, "claim")
    async with cluster.factory() as db:
        job = await db.get(AIGenerationJob, 22)
        assert job.status == "queued" and job.attempts == 0
        assert job.lifetime_attempts == 0 and job.claim_token is None
        assert (await db.get(AIListenTask, task_id)).queue_hold_reason is None


async def _add_generation_source(cluster, tweet_id: int):
    async with cluster.factory() as db:
        if await db.get(AIDataSource, 1) is None:
            db.add(
                AIDataSource(
                    id=1,
                    base_url="https://example.test",
                    model_name="test",
                    encrypted_api_key="test",
                    key_hint="test",
                    key_fingerprint="test",
                )
            )
        db.add(
            Tweet(
                id=tweet_id,
                tweet_id=f"source-{tweet_id}",
                monitored_user_id=1,
                author_id="a",
                text=f"source {tweet_id}",
                tweet_type="original",
                posted_at=datetime.now(UTC),
                raw_payload={},
            )
        )
        await db.commit()


async def test_same_account_tasks_keep_independent_skills_and_frozen_jobs(cluster, monkeypatch):
    from app.ai_worker import AIGenerationWorker
    from app.services.ai_jobs import enqueue_auto_jobs

    api = cluster.api
    second = (
        await api.post(
            "/api/v1/ai/skills", json={"name": "第二任务写作", "instructions": "第二任务指令"}
        )
    ).json()["id"]
    third = (
        await api.post(
            "/api/v1/ai/skills", json={"name": "第一任务新版", "instructions": "新版指令"}
        )
    ).json()["id"]
    tasks = []
    for name, ids in [("任务 A", [1]), ("任务 B", [second])]:
        response = await api.post(
            "/api/v1/ai/listen-tasks",
            json={
                "name": name,
                "monitored_user_ids": [1],
                "skill_ids": ids,
            },
        )
        assert response.status_code == 201, response.text
        tasks.append(response.json())
    await _add_generation_source(cluster, 201)
    async with cluster.factory() as db:
        assert await enqueue_auto_jobs(db, [201, 201]) == 2
        await db.commit()
        assert await enqueue_auto_jobs(db, [201]) == 0
        jobs = list(await db.scalars(select(AIGenerationJob).order_by(AIGenerationJob.id)))
        assert [j.listen_task_id for j in jobs] == [t["id"] for t in tasks]
        assert [j.skill_ids for j in jobs] == [[1], [second]]
        assert [j.skill_snapshot[0]["instructions"] for j in jobs] == [
            "原始指令",
            "第二任务指令",
        ]
        assert all(j.request_snapshot["skill_resolution"] == "listening_task" for j in jobs)
        assert len({j.idempotency_key for j in jobs}) == 2
        first_job_id, first_snapshot = jobs[0].id, jobs[0].skill_snapshot

    changed = await api.patch(
        f"/api/v1/ai/listen-tasks/{tasks[0]['id']}",
        json={
            "config_version": tasks[0]["config_version"],
            "skill_ids": [third, 1],
        },
    )
    assert changed.status_code == 200, changed.text
    assert (await cluster.client.task(tasks[0]["id"])).selected_ids == [third, 1]
    assert (await cluster.client.task(tasks[1]["id"])).selected_ids == [second]
    await _add_generation_source(cluster, 202)
    async with cluster.factory() as db:
        assert await enqueue_auto_jobs(db, [202]) == 2
        await db.commit()
        new_jobs = list(
            await db.scalars(
                select(AIGenerationJob)
                .where(
                    AIGenerationJob.source_tweet_id == 202,
                )
                .order_by(AIGenerationJob.id)
            )
        )
        assert [j.skill_ids for j in new_jobs] == [[third, 1], [second]]
        assert [s["instructions"] for s in new_jobs[0].skill_snapshot] == [
            "新版指令",
            "原始指令",
        ]
        assert (await db.get(AIGenerationJob, first_job_id)).skill_snapshot == first_snapshot

    # ai-worker validates the current task but consumes the job's frozen material.
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", cluster.factory)
    worker = object.__new__(AIGenerationWorker)
    worker.settings = cluster.client.settings
    worker.worker_id = "task-isolation-test"
    claimed = await worker._claim(first_job_id, "claim-original")
    assert claimed is not None
    assert claimed.skill_snapshot == first_snapshot
    assert claimed.skill_ids == [1]


async def test_manual_generation_requires_explicit_valid_skills_and_freezes_selection(cluster):
    api = cluster.api
    await _add_generation_source(cluster, 301)
    second = (
        await api.post(
            "/api/v1/ai/skills",
            json={
                "name": "手动选择",
                "instructions": "仅本次使用",
            },
        )
    ).json()["id"]
    path = "/api/v1/tweets/source-301/generate"
    for payload in (
        {},
        {"skill_ids": None},
        {"skill_ids": []},
        {"skill_ids": [1, 1]},
        {"skill_ids": [999999]},
    ):
        response = await api.post(path, json=payload)
        assert response.status_code == 422, response.text
    async with cluster.factory() as db:
        assert list(await db.scalars(select(AIGenerationJob))) == []
    payload = {"skill_ids": [second, 1], "idempotency_key": "explicit-manual-301"}
    generated = await api.post(path, json=payload)
    assert generated.status_code == 202, generated.text
    job_id = generated.json()["id"]
    assert (await api.post(path, json=payload)).json()["id"] == job_id
    async with cluster.factory() as db:
        job = await db.get(AIGenerationJob, job_id)
        assert job.listen_task_id is None and job.manual
        assert job.skill_ids == [second, 1]
        assert [s["instructions"] for s in job.skill_snapshot] == ["仅本次使用", "原始指令"]
        assert job.request_snapshot["skill_resolution"] == "manual_override"
    assert (await api.delete(f"/api/v1/ai/skills/{second}")).status_code == 200
    assert (await api.post(path, json={"skill_ids": [second]})).status_code == 422


async def test_historical_account_binding_snapshot_is_still_claimable(cluster, monkeypatch):
    from app.ai_worker import AIGenerationWorker

    await _add_generation_source(cluster, 401)
    response = await cluster.api.post(
        "/api/v1/tweets/source-401/generate",
        json={"skill_ids": [1]},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    async with cluster.factory() as db:
        job = await db.get(AIGenerationJob, job_id)
        job.manual = False
        job.trigger_type = "legacy_auto"
        job.request_snapshot = {**job.request_snapshot, "skill_resolution": "user_feature_binding"}
        snapshot = job.skill_snapshot
        await db.commit()
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", cluster.factory)
    worker = object.__new__(AIGenerationWorker)
    worker.settings = cluster.client.settings
    worker.worker_id = "historical-job-test"
    claimed = await worker._claim(job_id, "claim-historical")
    assert claimed is not None
    assert claimed.skill_snapshot == snapshot
    assert claimed.request_snapshot["skill_resolution"] == "user_feature_binding"
