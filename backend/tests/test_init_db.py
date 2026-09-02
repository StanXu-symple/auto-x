from contextlib import AbstractAsyncContextManager

from sqlalchemy.dialects import mysql

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
    def __init__(self) -> None:
        self.statements = []

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


async def test_seed_statements_are_race_safe_mysql_upserts(monkeypatch) -> None:
    session = FakeSession()
    monkeypatch.setattr(init_db, "AsyncSessionFactory", lambda: session)
    settings = Settings(_env_file=None)
    await init_db.seed_runtime_defaults(settings)
    # Administrator, polling/X source settings, three skills, AI feature/settings, XHS.
    assert len(session.statements) == 9
    for statement in session.statements:
        sql = str(statement.compile(dialect=mysql.dialect())).upper()
        assert "ON DUPLICATE KEY UPDATE" in sql
