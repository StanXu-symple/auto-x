from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app.ai_worker import AIGenerationWorker
from app.core.config import Settings
from app.models.ai import (
    AIFeature,
    AIGenerationAttempt,
    AIGenerationJob,
    AIListenTask,
    AIListenTaskSkill,
    AIListenTaskSubscription,
    AISetting,
    AISkill,
)
from app.models.ai_data_source import AIDataSource
from app.models.monitored_user import MonitoredUser
from app.models.tweet import Tweet
from app.services.ai_jobs import enqueue_listening_jobs


class ListenSession:
    def __init__(self, *, setting, tasks, tweets, users, subscriptions, links, skills, inserted):
        self.setting = setting
        self.tasks = tasks
        self.tweets = tweets
        self.users = users
        self.subscriptions = subscriptions
        self.links = links
        self.skills = skills
        self.inserted = inserted
        self.inserts = []
        self.events = []

    def add(self, item):
        self.events.append(item)

    async def scalar(self, _statement):
        return self.setting

    async def scalars(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect())).lower()
        if sql.startswith("insert into ai_generation_jobs"):
            self.inserts.append(statement)
            return self.inserted
        if "from tweets" in sql:
            return self.tweets
        if "from ai_listen_tasks" in sql:
            return self.tasks
        if "from monitored_users" in sql:
            return self.users
        if "from ai_listen_task_subscriptions" in sql:
            return self.subscriptions
        if "from ai_listen_task_skills" in sql:
            return self.links
        if "from ai_skills" in sql:
            return self.skills
        raise AssertionError(sql)


def make_listen_session(now: datetime, *, inserted: list[int]) -> ListenSession:
    setting = AISetting(
        id=1,
        enabled=True,
        auto_generate=True,
        auto_trigger_mode="listening_tasks",
        provider="openai_responses",
        model_name="test-model",
        base_url="https://example.test/v1",
        language="zh-CN",
        tone="自然",
        reasoning_effort="medium",
        default_skill_ids=[],
        max_attempts=3,
        max_output_tokens=1000,
        request_timeout_seconds=30,
    )
    task = AIListenTask(
        id=7,
        name="AI 产品",
        desired_state="enabled",
        all_monitored_users=False,
        listen_mode="original",
        feature_code="article_generation",
        config_version=4,
        activated_at=now - timedelta(hours=1),
        effective_from=now - timedelta(hours=1),
    )
    user_a = MonitoredUser(id=1, username="author_a", is_active=True)
    user_a.created_at = now - timedelta(days=3)
    user_b = MonitoredUser(id=2, username="author_b", is_active=True)
    user_b.created_at = now - timedelta(days=3)
    tweets = [
        Tweet(
            id=11,
            tweet_id="x11",
            monitored_user_id=1,
            author_id="a",
            text="new original",
            tweet_type="original",
            posted_at=now,
            raw_payload={},
        ),
        Tweet(
            id=12,
            tweet_id="x12",
            monitored_user_id=1,
            author_id="a",
            text="new reply",
            tweet_type="reply",
            posted_at=now,
            raw_payload={},
        ),
        Tweet(
            id=13,
            tweet_id="x13",
            monitored_user_id=1,
            author_id="a",
            text="old original",
            tweet_type="original",
            posted_at=now - timedelta(days=2),
            raw_payload={},
        ),
        Tweet(
            id=14,
            tweet_id="x14",
            monitored_user_id=2,
            author_id="b",
            text="other author",
            tweet_type="original",
            posted_at=now,
            raw_payload={},
        ),
    ]
    return ListenSession(
        setting=setting,
        tasks=[task],
        tweets=tweets,
        users=[user_a, user_b],
        subscriptions=[
            AIListenTaskSubscription(
                task_id=7,
                monitored_user_id=1,
                effective_from=now - timedelta(hours=1),
            )
        ],
        links=[
            AIListenTaskSkill(task_id=7, skill_id=2, priority=1),
            AIListenTaskSkill(task_id=7, skill_id=3, priority=2),
        ],
        skills=[
            AISkill(id=2, name="分析", instructions="skill two", is_active=True, version=1),
            AISkill(id=3, name="写作", instructions="skill three", is_active=True, version=1),
        ],
        inserted=inserted,
    )


async def test_listening_enqueue_matches_account_type_and_time_and_freezes_skills(
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    session = make_listen_session(now, inserted=[101])
    session.tasks[0].auto_publish_channels = ["xhs", "qq"]
    session.tasks[0].owner_admin_id = 9
    session.tasks[0].qq_bot_id = 2
    session.tasks[0].qq_group_openids = ["group-1"]
    feature = AIFeature(
        id=1,
        code="article_generation",
        name="写作",
        base_prompt="Write",
        is_active=True,
    )

    async def get_feature(*_args):
        return feature

    async def context(*_args, **_kwargs):
        return {"author": {"monitored_user_id": 1}}

    monkeypatch.setattr("app.services.ai_jobs.get_ai_feature", get_feature)
    monkeypatch.setattr("app.services.ai_jobs.build_author_context", context)

    count = await enqueue_listening_jobs(session, [11, 12, 13, 14])  # type: ignore[arg-type]
    assert count == 1
    assert len(session.inserts) == 1
    statement = session.inserts[0]
    sql = str(statement.compile(dialect=postgresql.dialect())).upper()
    params = statement.compile(dialect=postgresql.dialect()).params
    assert "ON CONFLICT (IDEMPOTENCY_KEY) DO NOTHING" in sql
    assert "RETURNING" in sql
    assert params["idempotency_key_m0"] == "listen:7:tweet:11"
    assert params["skill_ids_m0"] == [2, 3]
    assert [item["instructions"] for item in params["skill_snapshot_m0"]] == [
        "skill two",
        "skill three",
    ]
    assert params["task_config_version_m0"] == 4
    assert params["task_snapshot_m0"]["auto_publish_channels"] == ["xhs", "qq"]
    assert params["task_snapshot_m0"]["owner_admin_id"] == 9
    assert params["task_snapshot_m0"]["qq_bot_id"] == 2
    assert params["task_snapshot_m0"]["qq_group_openids"] == ["group-1"]
    assert session.events[0].event_type == "jobs_enqueued"
    assert session.events[0].details["count"] == 1


async def test_explicit_backfill_uses_requested_window_and_frozen_skill_snapshot(
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    session = make_listen_session(now, inserted=[103])
    session.tasks[0].auto_publish_channels = ["qq"]
    session.tasks[0].owner_admin_id = 9
    session.tasks[0].qq_bot_id = 2
    session.tasks[0].qq_group_openids = ["group-2"]
    session.tweets = [session.tweets[2]]
    feature = AIFeature(
        id=1,
        code="article_generation",
        name="写作",
        base_prompt="Write",
        is_active=True,
    )

    async def get_feature(*_args):
        return feature

    async def context(*_args, **_kwargs):
        return {"author": {"monitored_user_id": 1}}

    monkeypatch.setattr("app.services.ai_jobs.get_ai_feature", get_feature)
    monkeypatch.setattr("app.services.ai_jobs.build_author_context", context)
    snapshot = {
        "config_version": 2,
        "all_monitored_users": False,
        "monitored_user_ids": [1],
        "listen_mode": "original",
        "feature_code": "article_generation",
        "auto_publish_channels": ["xhs"],
        "owner_admin_id": 8,
        "qq_bot_id": 3,
        "qq_group_openids": ["group-old"],
        "skill_ids": [2, 3],
        "skills": [
            {"id": 2, "name": "分析", "instructions": "old two", "version": 1},
            {"id": 3, "name": "写作", "instructions": "old three", "version": 1},
        ],
        "language_override": "en",
        "exclude_legacy_generated": False,
    }
    count = await enqueue_listening_jobs(
        session,  # type: ignore[arg-type]
        [13],
        task_id=7,
        backfill_window=(now - timedelta(days=3), now - timedelta(days=1)),
        backfill_snapshot=snapshot,
    )
    assert count == 1
    params = session.inserts[0].compile(dialect=postgresql.dialect()).params
    assert params["idempotency_key_m0"] == "listen:7:tweet:13"
    assert params["skill_snapshot_m0"][0]["instructions"] == "old two"
    assert params["task_config_version_m0"] == 2
    assert params["task_snapshot_m0"]["auto_publish_channels"] == ["xhs"]
    assert params["task_snapshot_m0"]["owner_admin_id"] == 8
    assert params["task_snapshot_m0"]["qq_bot_id"] == 3
    assert params["task_snapshot_m0"]["qq_group_openids"] == ["group-old"]
    assert params["request_snapshot_m0"]["config"]["language"] == "en"

    # A backfill request saved before channel selection existed must remain opt-out.
    legacy_snapshot = {
        key: value
        for key, value in snapshot.items()
        if key not in {"auto_publish_channels", "owner_admin_id", "qq_bot_id", "qq_group_openids"}
    }
    await enqueue_listening_jobs(
        session,  # type: ignore[arg-type]
        [13],
        task_id=7,
        backfill_window=(now - timedelta(days=3), now - timedelta(days=1)),
        backfill_snapshot=legacy_snapshot,
    )
    legacy_params = session.inserts[1].compile(dialect=postgresql.dialect()).params
    assert legacy_params["task_snapshot_m0"]["auto_publish_channels"] == []
    assert legacy_params["task_snapshot_m0"]["owner_admin_id"] is None
    assert legacy_params["task_snapshot_m0"]["qq_bot_id"] is None
    assert legacy_params["task_snapshot_m0"]["qq_group_openids"] == []


@pytest.mark.parametrize(
    ("mode", "expected_ids"),
    [("reply", [15]), ("all", [15, 16])],
)
async def test_backfill_uses_stored_posts_even_after_collection_types_are_disabled(
    monkeypatch, mode: str, expected_ids: list[int]
) -> None:
    now = datetime.now(UTC)
    session = make_listen_session(now, inserted=list(range(101, 101 + len(expected_ids))))
    session.users[0].include_replies = False
    session.users[0].include_retweets = False
    session.tweets = [
        Tweet(
            id=15,
            tweet_id="x15",
            monitored_user_id=1,
            author_id="a",
            text="stored reply",
            tweet_type="reply",
            posted_at=now - timedelta(days=2),
            raw_payload={},
        ),
        Tweet(
            id=16,
            tweet_id="x16",
            monitored_user_id=1,
            author_id="a",
            text="stored retweet",
            tweet_type="retweet",
            posted_at=now - timedelta(days=2),
            raw_payload={},
        ),
    ]
    feature = AIFeature(
        id=1,
        code="article_generation",
        name="写作",
        base_prompt="Write",
        is_active=True,
    )

    async def get_feature(*_args):
        return feature

    async def context(*_args, **_kwargs):
        return {"author": {"monitored_user_id": 1}}

    monkeypatch.setattr("app.services.ai_jobs.get_ai_feature", get_feature)
    monkeypatch.setattr("app.services.ai_jobs.build_author_context", context)
    snapshot = {
        "config_version": 1,
        "all_monitored_users": False,
        "monitored_user_ids": [1],
        "listen_mode": mode,
        "feature_code": "article_generation",
        "skill_ids": [2, 3],
        "skills": [
            {"id": 2, "name": "分析", "instructions": "skill two", "version": 1},
            {"id": 3, "name": "写作", "instructions": "skill three", "version": 1},
        ],
        "exclude_legacy_generated": False,
    }

    count = await enqueue_listening_jobs(
        session,  # type: ignore[arg-type]
        [15, 16],
        task_id=7,
        backfill_window=(now - timedelta(days=3), now - timedelta(days=1)),
        backfill_snapshot=snapshot,
    )

    assert count == len(expected_ids)
    params = session.inserts[0].compile(dialect=postgresql.dialect()).params
    assert [params[f"source_tweet_id_m{index}"] for index in range(count)] == expected_ids


class AsyncContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *_args):
        return None


class PausedClaimSession:
    def __init__(self, job: AIGenerationJob, task: AIListenTask):
        self.job = job
        self.task = task
        self.scalar_calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def begin(self):
        return AsyncContext()

    async def scalar(self, _statement):
        self.scalar_calls += 1
        return self.task.id if self.scalar_calls == 1 else self.job

    async def get(self, entity, _id, **_kwargs):
        if entity is AISetting:
            return AISetting(id=1, enabled=True, auto_generate=True)
        if entity is AIListenTask:
            return self.task
        if entity is AIDataSource:
            return object()
        raise AssertionError(entity)


async def test_paused_task_does_not_start_a_queued_attempt(monkeypatch) -> None:
    now = datetime.now(UTC)
    task = AIListenTask(id=7, name="暂停", desired_state="paused")
    job = AIGenerationJob(
        id=8,
        listen_task_id=7,
        source_tweet_id=11,
        idempotency_key="listen:7:tweet:11",
        status="queued",
        attempts=0,
        lifetime_attempts=0,
        max_attempts=3,
        next_attempt_at=now - timedelta(seconds=1),
        manual=False,
    )
    session = PausedClaimSession(job, task)
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", lambda: session)
    worker = object.__new__(AIGenerationWorker)
    worker.settings = Settings(_env_file=None)
    worker.worker_id = "test-worker"
    assert await worker._claim(job.id, "claim") is None
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.lifetime_attempts == 0


class EnabledClaimSession(PausedClaimSession):
    def __init__(self, job: AIGenerationJob, task: AIListenTask):
        super().__init__(job, task)
        self.added = []
        self.expunge_called = False

    async def scalar(self, _statement):
        self.scalar_calls += 1
        return {
            1: self.task.id,
            2: self.job,
            3: 1,  # One current active Skill.
            4: 1,  # One previous retry round.
        }[self.scalar_calls]

    async def scalars(self, _statement):
        return [2]

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        return None

    def expunge(self, _job):
        self.expunge_called = True


async def test_manual_retry_claim_increments_lifetime_and_persists_attempt(monkeypatch) -> None:
    now = datetime.now(UTC)
    task = AIListenTask(id=7, name="运行", desired_state="enabled")
    job = AIGenerationJob(
        id=8,
        listen_task_id=7,
        source_tweet_id=11,
        idempotency_key="listen:7:tweet:11",
        status="queued",
        attempts=0,
        lifetime_attempts=2,
        max_attempts=3,
        next_attempt_at=now - timedelta(seconds=1),
        manual=False,
    )
    session = EnabledClaimSession(job, task)
    monkeypatch.setattr("app.ai_worker.AsyncSessionFactory", lambda: session)
    worker = object.__new__(AIGenerationWorker)
    worker.settings = Settings(_env_file=None, ai_worker_lock_ttl_seconds=60)
    worker.worker_id = "test-worker"
    assert await worker._claim(job.id, "claim") is job
    assert job.status == "running"
    assert job.attempts == 1
    assert job.lifetime_attempts == 3
    attempt = next(item for item in session.added if isinstance(item, AIGenerationAttempt))
    assert attempt.lifetime_number == 3
    assert attempt.round_number == 2
    assert attempt.attempt_number == 1
    assert attempt.status == "running"
    assert session.expunge_called
