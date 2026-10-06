from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import BigInteger, select, update
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.api.errors import APIError
from app.api.routes.ai import delete_ai_skill, patch_ai_skill
from app.api.routes.ai_listen import (
    _activate,
    _create_task,
    _out,
    archive_listen_task,
    copy_listen_task,
    create_listen_task,
    decide_listen_queue,
    patch_listen_task,
    pause_listen_task,
)
from app.db.base import Base
from app.models.admin import Admin
from app.models.ai import (
    AIFeature,
    AIGenerationJob,
    AIListenTaskBackfill,
    AISetting,
    AISkill,
)
from app.models.monitored_user import MonitoredUser
from app.models.qq import QQBotAccount, QQJoinedGroup
from app.models.tweet import Tweet
from app.schemas.ai import (
    AIListenTaskCreate,
    AIListenTaskPatch,
    AIListenTaskPreview,
    AIListenTaskQueueDecision,
    AISkillPatch,
)
from app.services.ai_listen_backfill import (
    _locked_backfill_users,
    process_pending_backfills,
    task_snapshot,
)
from app.services.ai_listen_tasks import get_task, preview_task


class FakeRedis:
    async def get(self, _key):
        return None


def test_backfill_account_precheck_holds_shared_locks_in_id_order() -> None:
    statement = _locked_backfill_users([3, 1])
    sql = str(statement.compile(dialect=postgresql.dialect())).upper()
    assert "ORDER BY MONITORED_USERS.ID" in sql
    assert sql.endswith("FOR SHARE")
    assert statement.get_execution_options()["populate_existing"] is True


@compiles(BigInteger, "sqlite")
def _sqlite_bigint(_type, _compiler, **_kw):
    return "INTEGER"


@pytest.fixture
async def database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        Base.metadata.tables[name]
        for name in (
            "admins",
            "qq_bot_accounts",
            "qq_joined_groups",
            "monitored_users",
            "ai_skills",
            "ai_features",
            "ai_settings",
            "ai_user_skill_bindings",
            "ai_listen_tasks",
            "ai_listen_task_subscriptions",
            "ai_listen_task_skills",
            "ai_listen_task_backfills",
            "ai_listen_task_events",
            "tweets",
            "ai_generation_jobs",
            "ai_data_sources",
        )
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as db:
        db.add_all(
            [
                AISetting(
                    id=1,
                    enabled=True,
                    auto_generate=True,
                    auto_trigger_mode="legacy_all",
                    provider="openai_responses",
                    model_name="model",
                    base_url="https://example.com",
                    language="zh-CN",
                    tone="自然",
                    reasoning_effort="medium",
                    default_skill_ids=[],
                    max_attempts=3,
                    max_output_tokens=1000,
                    request_timeout_seconds=30,
                ),
                AISkill(id=1, name="观点", instructions="原始指令", is_active=True, version=1),
                AIFeature(
                    id=1,
                    code="article_generation",
                    name="文章生成",
                    base_prompt="写草稿",
                    is_active=True,
                ),
                MonitoredUser(id=1, username="alice", is_active=True),
                Admin(id=9, username="publisher", password_hash="test"),
                QQBotAccount(
                    id=2,
                    name="写作机器人",
                    app_id="test-app",
                    encrypted_app_secret="test",
                    secret_hint="test",
                    secret_fingerprint="test",
                    is_enabled=True,
                ),
                QQJoinedGroup(
                    id=3,
                    bot_id=2,
                    app_id="test-app",
                    group_openid="group-1",
                    is_joined=True,
                    last_event_at=datetime.now(UTC),
                ),
            ]
        )
        await db.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


async def test_first_activation_switches_mode_and_freezes_initial_window(database) -> None:
    payload = AIListenTaskCreate(
        name="  AI 新闻  ",
        desired_state="enabled",
        all_monitored_users=False,
        monitored_user_ids=[1],
        listen_mode="original",
        skill_ids=[1],
        initial_sync_days=3,
    )
    async with database() as db:
        with pytest.raises(APIError) as error:
            await _create_task(db, payload)
        assert error.value.code == "legacy_mode_switch_required"
        await db.rollback()

        task = await _create_task(db, payload.model_copy(update={"switch_from_legacy": True}))
        await db.commit()
        assert task.desired_state == "enabled"
        assert task.activated_at is not None
        assert task.effective_from == task.activated_at
        assert task.initial_backfill_to == task.activated_at
        assert task.initial_backfill_from == task.activated_at - timedelta(days=3)
        assert (await db.get(AISetting, 1)).auto_trigger_mode == "listening_tasks"
        backfill = await db.scalar(select(AIListenTaskBackfill))
        assert backfill.request_id == "initial"
        assert backfill.config_snapshot["skills"][0]["instructions"] == "原始指令"


async def test_auto_publish_channels_persist_and_backfill_snapshot_survives_edit(database) -> None:
    async with database() as db:
        admin = await db.get(Admin, 9)
        created = await create_listen_task(
            AIListenTaskCreate(
                name="自动推送",
                desired_state="paused",
                monitored_user_ids=[1],
                skill_ids=[1],
                auto_publish_channels=["xhs", "qq"],
                qq_bot_id=2,
                qq_group_openids=["group-1"],
            ),
            db,
            FakeRedis(),
            admin,
        )
        task_id = created.id
        task = await get_task(db, task_id)
        assert task.owner_admin_id == admin.id
        assert created.auto_publish_channels == ["xhs", "qq"]
        assert (created.qq_bot_id, created.qq_group_openids) == (2, ["group-1"])
        frozen = task_snapshot(task)
        assert frozen["owner_admin_id"] == admin.id
        assert (frozen["qq_bot_id"], frozen["qq_group_openids"]) == (2, ["group-1"])
        copied = await copy_listen_task(task_id, db, FakeRedis(), admin)
        assert copied.auto_publish_channels == ["xhs", "qq"]
        assert (copied.qq_bot_id, copied.qq_group_openids) == (2, ["group-1"])
        assert (await get_task(db, copied.id)).owner_admin_id == admin.id
        edited = await patch_listen_task(
            task_id,
            AIListenTaskPatch(config_version=1, auto_publish_channels=[]),
            db,
            FakeRedis(),
            admin,
        )
        assert edited.auto_publish_channels == []
        assert edited.config_version == 2
        assert frozen["auto_publish_channels"] == ["xhs", "qq"]
        assert (await db.get(type(task), task_id)).auto_publish_channels == []


@pytest.mark.parametrize("channel", ["xhs", "qq"])
async def test_ownerless_task_binds_first_auto_publish_editor(database, channel: str) -> None:
    async with database() as db:
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="旧任务", desired_state="paused", monitored_user_ids=[1], skill_ids=[1]
            ),
        )
        await db.commit()
        assert task.owner_admin_id is None
        admin = await db.get(Admin, 9)
        await patch_listen_task(
            task.id,
            AIListenTaskPatch(
                config_version=1,
                auto_publish_channels=[channel],
                qq_bot_id=2 if channel == "qq" else None,
                qq_group_openids=["group-1"] if channel == "qq" else [],
            ),
            db,
            FakeRedis(),
            admin,
        )
        assert task.owner_admin_id == admin.id


async def test_other_admin_cannot_pause_archive_or_copy_owned_task(database) -> None:
    async with database() as db:
        db.add(Admin(id=10, username="other-admin", password_hash="test"))
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="归属管理员任务",
                desired_state="paused",
                monitored_user_ids=[1],
                skill_ids=[1],
            ),
            owner_admin_id=9,
        )
        await db.commit()
        other = await db.get(Admin, 10)
        for operation in (
            lambda: pause_listen_task(task.id, db, FakeRedis(), other),
            lambda: archive_listen_task(task.id, db, FakeRedis(), other),
            lambda: copy_listen_task(task.id, db, FakeRedis(), other),
        ):
            with pytest.raises(APIError) as error:
                await operation()
            assert error.value.status_code == 403
            assert error.value.code == "ai_listen_task_owner_required"
        assert (await get_task(db, task.id)).desired_state == "paused"


def test_auto_publish_channels_reject_unknown_duplicate_and_null() -> None:
    base = {"name": "推送", "monitored_user_ids": [1], "skill_ids": [1]}
    assert AIListenTaskCreate(**base).auto_publish_channels == []
    for channels in (["xhs", "xhs"], ["wechat"]):
        with pytest.raises(ValidationError):
            AIListenTaskCreate(**base, auto_publish_channels=channels)
    with pytest.raises(ValidationError):
        AIListenTaskPatch(config_version=1, auto_publish_channels=None)
    with pytest.raises(ValidationError):
        AIListenTaskCreate(**base, auto_publish_channels=["qq"])


async def test_qq_auto_publish_rejects_unjoined_groups_on_create_and_edit(database) -> None:
    async with database() as db:
        with pytest.raises(APIError) as error:
            await _create_task(
                db,
                AIListenTaskCreate(
                    name="无群",
                    desired_state="paused",
                    monitored_user_ids=[1],
                    skill_ids=[1],
                    auto_publish_channels=["qq"],
                    qq_bot_id=2,
                    qq_group_openids=["not-joined"],
                ),
            )
        assert error.value.code == "qq_group_not_joined"
        await db.rollback()
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="待绑定群",
                desired_state="paused",
                monitored_user_ids=[1],
                skill_ids=[1],
            ),
        )
        await db.commit()
        with pytest.raises(APIError) as error:
            await patch_listen_task(
                task.id,
                AIListenTaskPatch(
                    config_version=1,
                    auto_publish_channels=["qq"],
                    qq_bot_id=2,
                    qq_group_openids=["not-joined"],
                ),
                db,
                FakeRedis(),
                None,
            )
        assert error.value.code == "qq_group_not_joined"


async def test_paused_task_anchors_on_first_resume_only(database) -> None:
    payload = AIListenTaskCreate(
        name="暂停的任务",
        desired_state="paused",
        monitored_user_ids=[1],
        skill_ids=[1],
        initial_sync_days=1,
    )
    async with database() as db:
        task = await _create_task(db, payload)
        await db.commit()
        assert task.activated_at is None
        await _activate(db, task, switch_from_legacy=True)
        await db.commit()
        first = task.activated_at
        task.desired_state = "paused"
        await db.commit()
        await _activate(db, task, switch_from_legacy=False)
        await db.commit()
        assert task.activated_at == first
        assert len(list(await db.scalars(select(AIListenTaskBackfill)))) == 1


async def test_preview_counts_task_duplicates_and_legacy_without_ai_calls(database) -> None:
    now = datetime.now(UTC)
    async with database() as db:
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="预览任务",
                desired_state="paused",
                monitored_user_ids=[1],
                skill_ids=[1],
            ),
        )
        await db.flush()
        db.add_all(
            [
                Tweet(
                    id=10,
                    tweet_id="10",
                    monitored_user_id=1,
                    author_id="1",
                    text="one",
                    tweet_type="original",
                    posted_at=now - timedelta(hours=1),
                    raw_payload={},
                ),
                Tweet(
                    id=11,
                    tweet_id="11",
                    monitored_user_id=1,
                    author_id="1",
                    text="two",
                    tweet_type="original",
                    posted_at=now - timedelta(hours=2),
                    raw_payload={},
                ),
                Tweet(
                    id=12,
                    tweet_id="12",
                    monitored_user_id=1,
                    author_id="1",
                    text="reply",
                    tweet_type="reply",
                    posted_at=now - timedelta(hours=2),
                    raw_payload={},
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                AIGenerationJob(
                    id=20,
                    source_tweet_id=10,
                    listen_task_id=task.id,
                    trigger_type="listen_task",
                    skill_ids=[1],
                    skill_snapshot=[],
                    idempotency_key="listen:1:tweet:10",
                    status="queued",
                    provider="openai_responses",
                    model_name="model",
                    attempts=0,
                    max_attempts=3,
                    next_attempt_at=now,
                    manual=False,
                ),
                AIGenerationJob(
                    id=21,
                    source_tweet_id=11,
                    trigger_type="legacy_auto",
                    skill_ids=[1],
                    skill_snapshot=[],
                    idempotency_key="auto:11",
                    status="succeeded",
                    provider="openai_responses",
                    model_name="model",
                    attempts=1,
                    max_attempts=3,
                    next_attempt_at=now,
                    manual=False,
                ),
            ]
        )
        await db.commit()
        result = await preview_task(
            db,
            AIListenTaskPreview(
                name="预览任务",
                monitored_user_ids=[1],
                skill_ids=[1],
                task_id=task.id,
                from_at=now - timedelta(days=1),
                to_at=now,
            ),
        )
        assert (result.matched, result.duplicates, result.legacy_generated, result.pending) == (
            2,
            1,
            1,
            0,
        )


async def test_historical_preview_counts_saved_replies_with_current_collection_off(
    database,
) -> None:
    now = datetime.now(UTC)
    async with database() as db:
        user = await db.get(MonitoredUser, 1)
        user.include_replies = False
        db.add(
            Tweet(
                id=13,
                tweet_id="saved-reply",
                monitored_user_id=1,
                author_id="1",
                text="saved reply",
                tweet_type="reply",
                posted_at=now - timedelta(hours=1),
                raw_payload={},
            )
        )
        await db.commit()
        history = await preview_task(
            db,
            AIListenTaskPreview(
                name="历史回复",
                monitored_user_ids=[1],
                skill_ids=[1],
                listen_mode="reply",
                from_at=now - timedelta(days=1),
                to_at=now,
            ),
        )
        assert (history.matched, history.pending, history.unavailable_accounts) == (1, 1, [])
        realtime = await preview_task(
            db,
            AIListenTaskPreview(
                name="未来回复",
                monitored_user_ids=[1],
                skill_ids=[1],
                listen_mode="reply",
            ),
        )
        assert realtime.matched == 0
        assert realtime.unavailable_accounts[0]["reason"] == "未采集回复"


async def test_backfill_scan_is_bounded_and_pauses_without_losing_cursor(
    database, monkeypatch
) -> None:
    now = datetime.now(UTC)
    async with database() as db:
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="扫描任务",
                desired_state="enabled",
                switch_from_legacy=True,
                monitored_user_ids=[1],
                skill_ids=[1],
                initial_sync_days=1,
            ),
        )
        for index in range(3):
            db.add(
                Tweet(
                    id=100 + index,
                    tweet_id=f"scan{index}",
                    monitored_user_id=1,
                    author_id="1",
                    text=f"item {index}",
                    tweet_type="original",
                    posted_at=now - timedelta(hours=3 - index),
                    raw_payload={},
                )
            )
        await db.commit()
        seen = []

        async def fake_enqueue(_db, tweet_ids, **kwargs):
            seen.extend(tweet_ids)
            assert kwargs["backfill_snapshot"]["skill_ids"] == [1]
            return len(tweet_ids)

        monkeypatch.setattr("app.services.ai_jobs.enqueue_listening_jobs", fake_enqueue)
        assert await process_pending_backfills(db, batch_size=2) == 2
        await db.commit()
        task.desired_state = "paused"
        await db.commit()
        assert await process_pending_backfills(db, batch_size=2) == 0
        await db.commit()
        assert seen == [100, 101]
        task.desired_state = "enabled"
        await db.commit()
        assert await process_pending_backfills(db, batch_size=2) == 1
        await db.commit()
        backfill = await db.scalar(select(AIListenTaskBackfill))
        assert backfill.status == "completed"
        assert (backfill.scanned_count, backfill.enqueued_count) == (3, 3)
        assert seen == [100, 101, 102]


async def test_task_snapshot_keeps_skill_order_and_instruction(database) -> None:
    async with database() as db:
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="快照任务",
                desired_state="paused",
                monitored_user_ids=[1],
                skill_ids=[1],
            ),
        )
        await db.commit()
        task = await get_task(db, task.id)
        frozen = task_snapshot(task)
        assert frozen["skill_ids"] == [1]
        assert frozen["skills"][0]["instructions"] == "原始指令"
        skill = await db.get(AISkill, 1)
        skill.instructions = "后来修改"
        assert frozen["skills"][0]["instructions"] == "原始指令"


async def test_backfill_keeps_cursor_when_enqueue_discovers_skill_hold(
    database, monkeypatch
) -> None:
    now = datetime.now(UTC)
    async with database() as db:
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="失效技能",
                desired_state="enabled",
                switch_from_legacy=True,
                monitored_user_ids=[1],
                skill_ids=[1],
                initial_sync_days=1,
            ),
        )
        db.add(
            Tweet(
                id=70,
                tweet_id="hold70",
                monitored_user_id=1,
                author_id="1",
                text="source",
                tweet_type="original",
                posted_at=now - timedelta(hours=1),
                raw_payload={},
            )
        )
        await db.commit()

        async def hold_enqueue(_db, _tweet_ids, **_kwargs):
            task.queue_hold_reason = "skill_invalidated"
            return 0

        monkeypatch.setattr("app.services.ai_jobs.enqueue_listening_jobs", hold_enqueue)
        assert await process_pending_backfills(db, batch_size=10) == 0
        await db.commit()
        backfill = await db.scalar(select(AIListenTaskBackfill))
        assert backfill.cursor_tweet_id is None
        assert backfill.scanned_count == 0
        assert backfill.status == "pending"


async def test_backfill_refreshes_preloaded_cursor_before_scanning(database, monkeypatch) -> None:
    now = datetime.now(UTC)
    older = now - timedelta(hours=2)
    newer = now - timedelta(hours=1)
    async with database() as db:
        await _create_task(
            db,
            AIListenTaskCreate(
                name="游标刷新",
                desired_state="enabled",
                switch_from_legacy=True,
                monitored_user_ids=[1],
                skill_ids=[1],
                initial_sync_days=1,
            ),
        )
        db.add_all(
            [
                Tweet(
                    id=201,
                    tweet_id="cursor201",
                    monitored_user_id=1,
                    author_id="1",
                    text="old",
                    tweet_type="original",
                    posted_at=older,
                    raw_payload={},
                ),
                Tweet(
                    id=202,
                    tweet_id="cursor202",
                    monitored_user_id=1,
                    author_id="1",
                    text="new",
                    tweet_type="original",
                    posted_at=newer,
                    raw_payload={},
                ),
            ]
        )
        await db.commit()
        stale = await db.scalar(select(AIListenTaskBackfill))
        assert stale.cursor_tweet_id is None
        await db.execute(
            update(AIListenTaskBackfill)
            .where(AIListenTaskBackfill.id == stale.id)
            .values(cursor_tweet_id=201, cursor_posted_at=older, scanned_count=1)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        assert stale.cursor_tweet_id is None
        seen = []

        async def fake_enqueue(_db, tweet_ids, **_kwargs):
            seen.extend(tweet_ids)
            return len(tweet_ids)

        monkeypatch.setattr("app.services.ai_jobs.enqueue_listening_jobs", fake_enqueue)
        assert await process_pending_backfills(db, batch_size=10) == 1
        await db.commit()
        assert seen == [202]
        assert stale.scanned_count == 2


async def test_backfill_waits_for_account_and_feature_recovery(database, monkeypatch) -> None:
    now = datetime.now(UTC)
    async with database() as db:
        await _create_task(
            db,
            AIListenTaskCreate(
                name="等待采集",
                desired_state="enabled",
                switch_from_legacy=True,
                monitored_user_ids=[1],
                skill_ids=[1],
                initial_sync_days=1,
            ),
        )
        db.add(
            Tweet(
                id=301,
                tweet_id="wait301",
                monitored_user_id=1,
                author_id="1",
                text="old",
                tweet_type="original",
                posted_at=now - timedelta(hours=1),
                raw_payload={},
            )
        )
        await db.commit()
        seen = []

        async def fake_enqueue(_db, ids, **_kwargs):
            seen.extend(ids)
            return len(ids)

        monkeypatch.setattr("app.services.ai_jobs.enqueue_listening_jobs", fake_enqueue)
        user = await db.get(MonitoredUser, 1)
        user.is_active = False
        await db.commit()
        assert await process_pending_backfills(db, batch_size=10) == 0
        await db.commit()
        backfill = await db.scalar(select(AIListenTaskBackfill))
        assert backfill.cursor_tweet_id is None and backfill.last_error
        assert seen == []

        user.is_active = True
        feature = await db.get(AIFeature, 1)
        feature.is_active = False
        await db.commit()
        assert await process_pending_backfills(db, batch_size=10) == 0
        await db.commit()
        assert backfill.cursor_tweet_id is None and "功能点" in backfill.last_error
        feature.is_active = True
        await db.commit()
        assert await process_pending_backfills(db, batch_size=10) == 1
        await db.commit()
        assert seen == [301]
        assert backfill.status == "completed" and backfill.last_error is None


async def test_waiting_backfill_does_not_starve_another_task(database, monkeypatch) -> None:
    now = datetime.now(UTC)
    async with database() as db:
        db.add(MonitoredUser(id=2, username="bob", is_active=True))
        await db.commit()
        first = await _create_task(
            db,
            AIListenTaskCreate(
                name="等待的任务",
                desired_state="enabled",
                switch_from_legacy=True,
                monitored_user_ids=[1],
                skill_ids=[1],
                initial_sync_days=1,
            ),
        )
        second = await _create_task(
            db,
            AIListenTaskCreate(
                name="可执行任务",
                desired_state="enabled",
                monitored_user_ids=[2],
                skill_ids=[1],
                initial_sync_days=1,
            ),
        )
        db.add_all(
            [
                Tweet(
                    id=401,
                    tweet_id="first401",
                    monitored_user_id=1,
                    author_id="1",
                    text="first",
                    tweet_type="original",
                    posted_at=now - timedelta(hours=1),
                    raw_payload={},
                ),
                Tweet(
                    id=402,
                    tweet_id="second402",
                    monitored_user_id=2,
                    author_id="2",
                    text="second",
                    tweet_type="original",
                    posted_at=now - timedelta(hours=1),
                    raw_payload={},
                ),
            ]
        )
        await db.commit()
        user = await db.get(MonitoredUser, 1)
        user.is_active = False
        await db.commit()
        seen = []

        async def fake_enqueue(_db, ids, **_kwargs):
            seen.extend(ids)
            return len(ids)

        monkeypatch.setattr("app.services.ai_jobs.enqueue_listening_jobs", fake_enqueue)
        assert await process_pending_backfills(db, batch_size=10) == 1
        await db.commit()
        assert seen == [402]
        rows = list(
            await db.scalars(select(AIListenTaskBackfill).order_by(AIListenTaskBackfill.id))
        )
        assert [row.task_id for row in rows] == [first.id, second.id]
        assert rows[0].status == "pending" and rows[0].cursor_tweet_id is None
        assert rows[1].status == "completed" and rows[1].enqueued_count == 1


async def test_backfill_scans_saved_reply_with_current_collection_off(
    database, monkeypatch
) -> None:
    now = datetime.now(UTC)
    async with database() as db:
        await _create_task(
            db,
            AIListenTaskCreate(
                name="已存回复",
                desired_state="enabled",
                switch_from_legacy=True,
                monitored_user_ids=[1],
                skill_ids=[1],
                listen_mode="reply",
                initial_sync_days=1,
            ),
        )
        user = await db.get(MonitoredUser, 1)
        user.include_replies = False
        db.add(
            Tweet(
                id=501,
                tweet_id="saved501",
                monitored_user_id=1,
                author_id="1",
                text="reply",
                tweet_type="reply",
                posted_at=now - timedelta(hours=1),
                raw_payload={},
            )
        )
        await db.commit()
        seen = []

        async def fake_enqueue(_db, ids, **_kwargs):
            seen.extend(ids)
            return len(ids)

        monkeypatch.setattr("app.services.ai_jobs.enqueue_listening_jobs", fake_enqueue)
        assert await process_pending_backfills(db, batch_size=10) == 1
        await db.commit()
        assert seen == [501]


async def test_queue_decision_and_archive_keep_job_audit(database) -> None:
    now = datetime.now(UTC)
    async with database() as db:
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="审计任务",
                desired_state="enabled",
                switch_from_legacy=True,
                monitored_user_ids=[1],
                skill_ids=[1],
            ),
        )
        db.add(
            Tweet(
                id=80,
                tweet_id="audit80",
                monitored_user_id=1,
                author_id="1",
                text="source",
                tweet_type="original",
                posted_at=now,
                raw_payload={},
            )
        )
        await db.flush()
        db.add(
            AIGenerationJob(
                id=90,
                source_tweet_id=80,
                listen_task_id=task.id,
                trigger_type="listen_task",
                skill_ids=[1],
                skill_snapshot=[],
                idempotency_key=f"listen:{task.id}:tweet:80",
                status="queued",
                provider="openai_responses",
                model_name="model",
                attempts=0,
                lifetime_attempts=2,
                max_attempts=3,
                next_attempt_at=now,
                manual=False,
            )
        )
        task.queue_hold_reason = "skill_invalidated"
        await db.commit()
        decided = await decide_listen_queue(
            task.id,
            AIListenTaskQueueDecision(decision="continue_old_snapshot"),
            db,
            FakeRedis(),
            None,
        )
        assert decided.queue_hold_reason is None
        assert decided.stats.queued == 1
        archived = await archive_listen_task(task.id, db, FakeRedis(), None)
        assert archived.desired_state == "archived"
        assert archived.stats.matched == 1
        assert archived.stats.cancelled == 1
        assert archived.stats.lifetime_attempts == 2
        assert (await db.get(AIGenerationJob, 90)).status == "cancelled"


async def test_patch_reuses_skill_links_and_reorders_without_unique_conflict(database) -> None:
    async with database() as db:
        db.add(AISkill(id=2, name="摘要", instructions="摘要指令", is_active=True, version=1))
        await db.commit()
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="编辑技能",
                desired_state="paused",
                monitored_user_ids=[1],
                skill_ids=[1, 2],
            ),
        )
        await db.commit()
        old_link_ids = {link.skill_id: link.id for link in task.skills}
        edited = await patch_listen_task(
            task.id,
            AIListenTaskPatch(config_version=1, skill_ids=[2, 1]),
            db,
            FakeRedis(),
            None,
        )
        assert edited.skill_ids == [2, 1]
        assert {link.skill_id: link.id for link in task.skills} == old_link_ids
        edited = await patch_listen_task(
            task.id,
            AIListenTaskPatch(config_version=2, skill_ids=[2]),
            db,
            FakeRedis(),
            None,
        )
        assert edited.skill_ids == [2]
        edited = await patch_listen_task(
            task.id,
            AIListenTaskPatch(config_version=3, skill_ids=[1, 2]),
            db,
            FakeRedis(),
            None,
        )
        assert edited.skill_ids == [1, 2]


async def test_explicit_archived_account_remains_visible_in_task_config(database) -> None:
    async with database() as db:
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="历史账号",
                desired_state="paused",
                monitored_user_ids=[1],
                skill_ids=[1],
            ),
        )
        await db.commit()
        user = await db.get(MonitoredUser, 1)
        user.archived_at = datetime.now(UTC)
        user.is_active = False
        await db.commit()
        result = await _out(db, FakeRedis(), await get_task(db, task.id))
        assert result.accounts[0].username == "alice"
        assert result.accounts[0].archived_at is not None
        assert result.health.status == "blocked"


async def test_task_read_survives_redis_failure(database) -> None:
    class BrokenRedis:
        async def get(self, _key):
            raise ConnectionError("offline")

    async with database() as db:
        task = await _create_task(
            db,
            AIListenTaskCreate(
                name="只读可用",
                desired_state="paused",
                monitored_user_ids=[1],
                skill_ids=[1],
            ),
        )
        await db.commit()
        result = await _out(db, BrokenRedis(), await get_task(db, task.id))
        assert result.name == "只读可用"
        assert "AI Worker 离线" in result.dependency.reasons


def test_task_patch_rejects_null_account_selection() -> None:
    with pytest.raises(ValidationError):
        AIListenTaskPatch(config_version=1, monitored_user_ids=None)


@pytest.mark.parametrize("operation", ["patch", "delete"])
async def test_skill_deactivation_takes_setting_lock_before_skill(
    database, monkeypatch, operation
) -> None:
    from app.services.ai_jobs import get_ai_setting as real_get_setting

    sequence = []
    real_get = AsyncSession.get

    async def setting_spy(*args, **kwargs):
        sequence.append("setting")
        return await real_get_setting(*args, **kwargs)

    async def get_spy(self, entity, ident, **kwargs):
        if entity is AISkill:
            sequence.append("skill")
        return await real_get(self, entity, ident, **kwargs)

    monkeypatch.setattr("app.api.routes.ai.get_ai_setting", setting_spy)
    monkeypatch.setattr(AsyncSession, "get", get_spy)
    async with database() as db:
        if operation == "patch":
            await patch_ai_skill(1, AISkillPatch(is_active=False), db, None)
        else:
            await delete_ai_skill(1, db, None)
    assert sequence[:2] == ["setting", "skill"]
