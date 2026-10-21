"""Execute the destructive migration against isolated data and verify its boundary."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_removing_legacy_selection_preserves_tasks_and_historical_job_snapshots():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0038_explicit_task_skills.py"
    spec = importlib.util.spec_from_file_location("explicit_skills_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData()
    settings = sa.Table(
        "ai_settings",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("enabled", sa.Boolean),
        sa.Column("auto_generate", sa.Boolean),
        sa.Column("default_skill_ids", sa.JSON),
        sa.Column("auto_trigger_mode", sa.String(24)),
    )
    bindings = sa.Table(
        "ai_user_skill_bindings",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("monitored_user_id", sa.Integer),
        sa.Column("ai_feature_id", sa.Integer),
        sa.Column("skill_id", sa.Integer),
    )
    jobs = sa.Table(
        "ai_generation_jobs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String),
        sa.Column("skill_snapshot", sa.JSON),
        sa.Column("request_snapshot", sa.JSON),
    )
    selections = sa.Table(
        "ai_listen_task_skills",
        metadata,
        sa.Column("task_id", sa.Integer, primary_key=True),
        sa.Column("skill_id", sa.Integer),
    )
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            metadata.create_all(connection)
            connection.execute(
                settings.insert(),
                [
                    dict(
                        id=1,
                        enabled=True,
                        auto_generate=True,
                        default_skill_ids=[7],
                        auto_trigger_mode="legacy_all",
                    ),
                    dict(
                        id=2,
                        enabled=True,
                        auto_generate=False,
                        default_skill_ids=[8],
                        auto_trigger_mode="listening_tasks",
                    ),
                ],
            )
            connection.execute(
                bindings.insert().values(
                    id=1,
                    monitored_user_id=5,
                    ai_feature_id=1,
                    skill_id=7,
                )
            )
            frozen = [{"id": 7, "instructions": "historical prompt", "version": 3}]
            request = {"skill_resolution": "user_feature_binding", "skills": frozen}
            connection.execute(
                jobs.insert().values(
                    id=10,
                    status="queued",
                    skill_snapshot=frozen,
                    request_snapshot=request,
                )
            )
            connection.execute(
                selections.insert(),
                [
                    {"task_id": 20, "skill_id": 7},
                    {"task_id": 21, "skill_id": 8},
                ],
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            inspector = sa.inspect(connection)
            assert "ai_user_skill_bindings" not in inspector.get_table_names()
            assert {c["name"] for c in inspector.get_columns("ai_settings")} == {
                "id",
                "enabled",
                "auto_generate",
            }
            assert connection.execute(
                sa.text("SELECT enabled, auto_generate FROM ai_settings ORDER BY id")
            ).all() == [(1, 1), (1, 0)]
            assert connection.execute(sa.select(jobs)).mappings().one() == {
                "id": 10,
                "status": "queued",
                "skill_snapshot": frozen,
                "request_snapshot": request,
            }
            assert connection.execute(sa.select(selections)).all() == [(20, 7), (21, 8)]
    finally:
        engine.dispose()
