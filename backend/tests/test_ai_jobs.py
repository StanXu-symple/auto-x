from datetime import UTC, datetime

import pytest

from app.models.ai import AISetting
from app.models.tweet import Tweet
from app.services.ai_jobs import enqueue_auto_jobs


class FakeSession:
    def __init__(self, setting, scalar_batches):
        self.setting = setting
        self.scalar_batches = list(scalar_batches)
        self.statements = []

    async def scalar(self, _statement):
        return self.setting

    async def scalars(self, statement):
        self.statements.append(statement)
        return self.scalar_batches.pop(0)


async def test_auto_enqueue_requires_an_enabled_listening_task() -> None:
    setting = AISetting(id=1, enabled=True, auto_generate=True)
    tweet = Tweet(id=11, monitored_user_id=1, posted_at=datetime.now(UTC))
    session = FakeSession(setting, [[tweet], []])
    assert await enqueue_auto_jobs(session, [11]) == 0
    assert len(session.statements) == 2
    assert all(not statement.is_insert for statement in session.statements)


@pytest.mark.parametrize("enabled,auto_generate", [(False, True), (True, False)])
async def test_auto_enqueue_stays_off_when_disabled(enabled, auto_generate) -> None:
    session = FakeSession(AISetting(id=1, enabled=enabled, auto_generate=auto_generate), [])
    assert await enqueue_auto_jobs(session, [1]) == 0
    assert session.statements == []
