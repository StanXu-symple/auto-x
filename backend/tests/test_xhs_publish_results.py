"""Completion notifications recover late results without publishing twice."""

from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.routes.xhs_results import router
from app.control_plane.security import ISSUER, ServiceVerifier
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    Admin,
    AIDraft,
    AIGenerationJob,
    AIPublishDispatch,
    ArticlePublishAttempt,
    BrowserJobResult,
)
from app.services.browser_job_store import BrowserJobStore
from app.services.xhs_publish_results import apply_xhs_job_result

JOB_ID = "a" * 32
NOTE_URL = "https://www.xiaohongshu.com/explore/abcdef012345678901234567"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session, session.begin():
        session.add(Admin(id=9, username="owner", password_hash="unused"))
        session.add(AIGenerationJob(
            id=7, source_tweet_id=1, idempotency_key="result-job", skill_ids=[],
            skill_snapshot=[], status="succeeded", provider="openai_responses", model_name="test",
        ))
        session.add(AIDraft(
            id=20, job_id=7, article_source="ai", title="文章", content="正文", revision=1,
            publish_status="published", publish_channel="qq", publish_attempt_id="qq-attempt",
        ))
        session.add(ArticlePublishAttempt(
            attempt_id="xhs-attempt", article_id=20, channel="xhs", status="queued",
            target_summary="当前账号",
        ))
        session.add(AIPublishDispatch(
            id=15, job_id=7, draft_id=20, channel="xhs", status="reconciling", attempts=1,
            owner_admin_id=9, article_publish_attempt_id="xhs-attempt", xhs_job_id=JOB_ID,
            payload_snapshot={"owner_admin_id": 9, "draft_revision": 1},
            last_error="等待浏览器执行结果",
        ))
    try:
        yield factory
    finally:
        await engine.dispose()


async def complete(store, state="succeeded", error=None):
    await store.accept("xhs", JOB_ID, 9, "fingerprint", lease_seconds=300)
    return await store.finish(
        "xhs", JOB_ID, state, {"result": {"success": True, "url": NOTE_URL}}, error
    )


async def test_late_success_is_applied_once_and_keeps_other_channel(factory):
    await complete(BrowserJobStore(factory))
    async with factory() as session, session.begin():
        assert await apply_xhs_job_result(session, JOB_ID)
    async with factory() as session, session.begin():
        assert not await apply_xhs_job_result(session, JOB_ID)
        dispatch = await session.get(AIPublishDispatch, 15)
        assert dispatch.status == "published"
        assert dispatch.last_error is None
        assert dispatch.published_url == NOTE_URL
        attempt = await session.get(ArticlePublishAttempt, "xhs-attempt")
        assert attempt.status == "published"
        assert attempt.published_url == NOTE_URL
        draft = await session.get(AIDraft, 20)
        assert (draft.publish_channel, draft.publish_attempt_id) == ("qq", "qq-attempt")


async def test_verified_failure_is_distinct_from_uncertainty(factory):
    await complete(BrowserJobStore(factory), "failed", "标题填写失败，未提交发布")
    async with factory() as session, session.begin():
        assert await apply_xhs_job_result(session, JOB_ID)
        dispatch = await session.get(AIPublishDispatch, 15)
        assert dispatch.status == "failed"
        assert dispatch.last_error == "标题填写失败，未提交发布"


@pytest.mark.parametrize("url", [
    "https://creator.xiaohongshu.com/publish/publish?source=official",
    "https://www.xiaohongshu.com/explore",
    "https://www.xiaohongshu.com/user/profile/abcdef012345678901234567",
    "https://xiaohongshu.com.evil.example/explore/abcdef012345678901234567",
])
async def test_success_does_not_show_creator_or_non_note_url_as_published_note(factory, url):
    store = BrowserJobStore(factory)
    await store.accept("xhs", JOB_ID, 9, "fingerprint")
    await store.finish("xhs", JOB_ID, "succeeded", {"result": {"url": url}})
    async with factory() as session, session.begin():
        assert await apply_xhs_job_result(session, JOB_ID)
        dispatch = await session.get(AIPublishDispatch, 15)
        assert dispatch.status == "published"
        assert dispatch.published_url is None


@pytest.mark.parametrize("condition", ["manual", "new_attempt", "wrong_admin"])
async def test_callback_preserves_manual_decision_or_newer_attempt(factory, condition):
    await complete(BrowserJobStore(factory))
    async with factory() as session, session.begin():
        dispatch = await session.get(AIPublishDispatch, 15)
        if condition == "manual":
            dispatch.status = "uncertain"
            dispatch.reconciled_at = datetime.now(UTC)
            dispatch.reconciliation_result = "uncertain"
        elif condition == "new_attempt":
            dispatch.xhs_job_id = "b" * 32
        else:
            dispatch.owner_admin_id = 10
    async with factory() as session, session.begin():
        assert not await apply_xhs_job_result(session, JOB_ID)
        assert (await session.get(AIPublishDispatch, 15)).status != "published"


async def test_canonical_state_is_idempotent_and_uncertain_can_be_corrected(factory):
    store = BrowserJobStore(factory)
    row, created = await store.accept("xhs", JOB_ID, 9, "fingerprint", lease_seconds=300)
    assert created and row.state == "running"
    assert not (await store.accept("xhs", JOB_ID, 9, "fingerprint"))[1]
    with pytest.raises(ValueError, match="different request"):
        await store.accept("xhs", JOB_ID, 9, "different")
    await store.finish("xhs", JOB_ID, "uncertain", error="执行连接断开")
    async with factory() as session, session.begin():
        assert await apply_xhs_job_result(session, JOB_ID)
    await store.mark_callback(JOB_ID, True, state="uncertain")
    assert await store.callback_jobs() == []
    await store.finish("xhs", JOB_ID, "succeeded", {"url": NOTE_URL})
    # An older callback response must not suppress the newly known success.
    await store.mark_callback(JOB_ID, True, state="uncertain")
    assert len(await store.callback_jobs()) == 1
    async with factory() as session, session.begin():
        assert await apply_xhs_job_result(session, JOB_ID)
        assert (await session.get(AIPublishDispatch, 15)).status == "published"
    await store.finish("xhs", JOB_ID, "failed", error="stale timeout")
    assert (await store.get("xhs", JOB_ID)).state == "succeeded"


async def test_completion_outbox_survives_store_restart_and_backs_off(factory):
    store = BrowserJobStore(factory)
    await complete(store)
    restarted = BrowserJobStore(factory)
    assert len(await restarted.callback_jobs()) == 1
    await restarted.mark_callback(JOB_ID, False, state="succeeded")
    assert await restarted.callback_jobs() == []
    saved = await restarted.get("xhs", JOB_ID)
    assert saved.callback_attempts == 1
    assert saved.callback_delivered_at is None
    async with factory() as session, session.begin():
        row = await session.get(BrowserJobResult, ("xhs", JOB_ID))
        row.next_callback_at = datetime.now(UTC) - timedelta(seconds=1)
    assert len(await restarted.callback_jobs()) == 1
    await restarted.mark_callback(JOB_ID, True, state="succeeded")
    assert await restarted.callback_jobs() == []


async def test_recovery_only_selects_expired_active_jobs(factory):
    store = BrowserJobStore(factory)
    await store.accept("camoufox", JOB_ID, 9, "a", worker_id="worker-1", lease_seconds=300)
    await store.accept("camoufox", "b" * 32, 9, "b", lease_seconds=-1)
    assert [r.job_id for r in await store.recovery_jobs("camoufox")] == ["b" * 32]
    await store.renew("camoufox", JOB_ID, "worker-1", -1)
    assert [r.job_id for r in await store.recovery_jobs("camoufox")] == [JOB_ID]


@pytest.mark.parametrize("namespace", ["xhs", "camoufox"])
async def test_old_unresolved_jobs_cannot_starve_new_expired_job(factory, namespace):
    now = datetime.now(UTC)
    async with factory() as session, session.begin():
        for number in range(101):
            session.add(BrowserJobResult(
                namespace=namespace, job_id=f"{number:032x}", admin_id=9,
                fingerprint="old", state="uncertain", data={},
                updated_at=now - timedelta(days=1),
            ))
    store = BrowserJobStore(factory)
    await store.accept(namespace, JOB_ID, 9, "new", lease_seconds=-1)
    batches = [await store.recovery_jobs(namespace), await store.recovery_jobs(namespace)]
    assert sum(row.job_id == JOB_ID for batch in batches for row in batch) == 1
    if namespace == "camoufox":
        assert sum(len(batch) for batch in batches) == 1


async def test_recovery_does_not_expire_a_lease_renewed_after_selection(factory):
    store = BrowserJobStore(factory)
    await store.accept(
        "camoufox", JOB_ID, 9, "fingerprint", worker_id="executor", lease_seconds=-1
    )
    selected = await store.recovery_jobs("camoufox")
    assert [row.job_id for row in selected] == [JOB_ID]
    await store.renew("camoufox", JOB_ID, "executor", 300)
    outcome = await store.finish(
        "camoufox", JOB_ID, "uncertain", error="stale observer",
        require_expired_lease=True,
    )
    assert outcome.state == "running"
    assert outcome.error is None
    await store.renew("camoufox", JOB_ID, "executor", -1)
    outcome = await store.finish(
        "camoufox", JOB_ID, "uncertain", error="expired executor",
        require_expired_lease=True,
    )
    assert outcome.state == "uncertain"


@pytest.fixture
def signer():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()

    def token(**overrides):
        now = datetime.now(UTC)
        claims = {
            "sub": "xhs-worker", "aud": "backend", "iss": ISSUER, "iat": now,
            "nbf": now, "exp": now + timedelta(minutes=5), "jti": "test-token",
            "kid": "key-1", "scope": "xhs:result", "type": "service",
        }
        claims.update(overrides)
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "key-1"})
    return public_key, token


async def client_for(factory, signer):
    app = FastAPI()
    app.state.xhs_result_verifier = ServiceVerifier(signer[0], "backend", "xhs:result")
    app.include_router(router, prefix="/api/v1")

    async def database():
        async with factory() as session:
            yield session
    app.dependency_overrides[get_db] = database
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api")


@pytest.mark.parametrize("overrides,expected", [
    ({"sub": "backend"}, 403),
    ({"scope": "xhs:execute"}, 401),
    ({"type": "admin"}, 401),
    ({"aud": "camoufox-worker"}, 401),
])
async def test_callback_rejects_wrong_identity_scope_or_token_type(
    factory, signer, overrides, expected,
):
    await complete(BrowserJobStore(factory))
    async with await client_for(factory, signer) as client:
        response = await client.post(
            f"/api/v1/internal/xhs/jobs/{JOB_ID}/completion",
            headers={"Authorization": f"Bearer {signer[1](**overrides)}"},
        )
    assert response.status_code == expected
    async with factory() as session:
        assert (await session.get(AIPublishDispatch, 15)).status == "reconciling"


async def test_callback_uses_db_outcome_ignores_body_and_accepts_duplicates(factory, signer):
    await complete(BrowserJobStore(factory), "failed", "上传图片失败")
    async with await client_for(factory, signer) as client:
        for applied in (True, False):
            response = await client.post(
                f"/api/v1/internal/xhs/jobs/{JOB_ID}/completion",
                headers={"Authorization": f"Bearer {signer[1]()}"},
                json={"state": "succeeded", "admin_id": 100},
            )
            assert response.status_code == 200
            assert response.json() == {"accepted": True, "applied": applied}
    async with factory() as session:
        assert (await session.get(AIPublishDispatch, 15)).status == "failed"
