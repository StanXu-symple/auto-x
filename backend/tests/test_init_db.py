from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.core.config import Settings
from app.db import init_db


class AsyncContext(AbstractAsyncContextManager):
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return None


class FakeSession:
    def __init__(self, seed_claim="seed-claimed") -> None:
        self.statements = []
        self.seed_claim = seed_claim

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def begin(self):
        return AsyncContext(None)

    async def scalar(self, _statement):
        return None

    async def get(self, *_args):
        return None

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(scalar_one_or_none=lambda: self.seed_claim)


async def test_seed_statements_are_race_safe_postgres_upserts(monkeypatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(init_db, "AsyncSessionFactory", lambda: session)
    settings = Settings(_env_file=None)
    await init_db.seed_runtime_defaults(settings)
    # Administrator, polling/X source settings, three skills, AI feature/settings.
    assert len(session.statements) == 10
    for statement in session.statements:
        sql = str(statement.compile(dialect=postgresql.dialect())).upper()
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql


async def test_existing_seed_marker_does_not_restore_deleted_placeholders(monkeypatch):
    session = FakeSession(seed_claim=None)
    monkeypatch.setattr(init_db, "AsyncSessionFactory", lambda: session)
    await init_db.seed_runtime_defaults(Settings(_env_file=None))
    assert len(session.statements) == 9
    assert not any(statement.table.name == "qq_placeholders" for statement in session.statements)
