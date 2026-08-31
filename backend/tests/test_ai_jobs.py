from datetime import UTC, datetime

from sqlalchemy.dialects import mysql

from app.models.ai import AISetting, AISkill
from app.models.tweet import Tweet
from app.services.ai_jobs import enqueue_auto_jobs


class FakeSession:
    def __init__(self, setting, scalar_batches):
        self.setting = setting
        self.scalar_batches = list(scalar_batches)
        self.statements = []

    async def scalar(self, _statement):
        return self.setting

    async def scalars(self, _statement):
        return self.scalar_batches.pop(0)

    async def execute(self, statement):
        self.statements.append(statement)


async def test_auto_enqueue_is_idempotent_and_freezes_skill_audit_snapshot() -> None:
    now = datetime.now(UTC)
    setting = AISetting(
        id=1,
        enabled=True,
        auto_generate=True,
        provider="openai_responses",
        model_name="gpt-5.6-terra",
        base_url="https://api.openai.com/v1",
        bridge_url=None,
        language="zh-CN",
        tone="专业自然",
        require_review=True,
        reasoning_effort="medium",
        default_skill_ids=[2],
        max_attempts=3,
        max_output_tokens=1200,
        request_timeout_seconds=30,
    )
    skill = AISkill(
        id=2,
        name="中文短帖",
        description="desc",
        instructions="frozen instructions",
        output_schema=None,
        is_active=True,
        version=7,
    )
    tweet_1 = Tweet(
        id=11,
        tweet_id="x11",
        monitored_user_id=1,
        author_id="author",
        text="first",
        posted_at=now,
        raw_payload={},
    )
    tweet_2 = Tweet(
        id=12,
        tweet_id="x12",
        monitored_user_id=1,
        author_id="author",
        text="second",
        posted_at=now,
        raw_payload={},
    )
    session = FakeSession(setting, [[skill], [tweet_1, tweet_2], ["auto:11"]])
    inserted = await enqueue_auto_jobs(session, [11, 12])  # type: ignore[arg-type]

    assert inserted == 1
    assert len(session.statements) == 1
    statement = session.statements[0]
    sql = str(statement.compile(dialect=mysql.dialect())).upper()
    params = statement.compile(dialect=mysql.dialect()).params
    assert "ON DUPLICATE KEY UPDATE" in sql
    snapshots = [value for key, value in params.items() if key.startswith("skill_snapshot")]
    assert snapshots[0][0]["instructions"] == "frozen instructions"
    request_snapshots = [
        value for key, value in params.items() if key.startswith("request_snapshot")
    ]
    assert request_snapshots[0]["config"]["model"] == "gpt-5.6-terra"
    assert request_snapshots[0]["source"]["text"] == "first"


async def test_auto_enqueue_stays_off_when_disabled() -> None:
    setting = AISetting(
        id=1,
        enabled=False,
        auto_generate=True,
        provider="openai_responses",
        model_name="gpt-5.6-terra",
        base_url="https://api.openai.com/v1",
        language="zh-CN",
        tone="专业自然",
        require_review=True,
        reasoning_effort="medium",
        default_skill_ids=[1],
        max_attempts=3,
        max_output_tokens=1200,
        request_timeout_seconds=30,
    )
    session = FakeSession(setting, [])
    assert await enqueue_auto_jobs(session, [1]) == 0  # type: ignore[arg-type]
    assert session.statements == []
