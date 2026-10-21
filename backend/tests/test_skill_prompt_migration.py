"""Classification must leave sources, task selections and frozen jobs intact."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def test_classification_preserves_existing_data_and_downgrades_cleanly():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0039_skill_prompt_types.py"
    spec = importlib.util.spec_from_file_location("skill_prompt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    metadata = sa.MetaData()
    skills = sa.Table(
        "ai_skills",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_type", sa.String(16)),
        sa.Column("name", sa.String),
        sa.Column("instructions", sa.Text),
        sa.Column("version", sa.Integer),
        sa.Column("is_active", sa.Boolean),
        sa.Column("source_key", sa.String),
        sa.Column("source_metadata", sa.JSON),
    )
    selections = sa.Table(
        "ai_listen_task_skills",
        metadata,
        sa.Column("task_id", sa.Integer, primary_key=True),
        sa.Column("skill_id", sa.Integer, primary_key=True),
        sa.Column("priority", sa.Integer),
    )
    jobs = sa.Table(
        "ai_generation_jobs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("status", sa.String),
        sa.Column("skill_snapshot", sa.JSON),
        sa.Column("request_snapshot", sa.JSON),
    )
    engine = sa.create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            metadata.create_all(connection)
            rows = [
                dict(
                    id=index,
                    name=f"Instruction {index}",
                    instructions=f"Original {index}",
                    version=index + 1,
                    source_type=source,
                    is_active=active,
                    source_key=f"source-{index}" if source == "github" else None,
                    source_metadata={"commit": "a" * 40, "bundle_id": "original"}
                    if source == "github"
                    else None,
                )
                for index, (source, active) in enumerate(
                    [("custom", True), ("github", True), ("custom", False), ("github", False)],
                    start=1,
                )
            ]
            connection.execute(skills.insert(), rows)
            connection.execute(
                selections.insert(),
                [
                    {"task_id": 9, "skill_id": 1, "priority": 0},
                    {"task_id": 9, "skill_id": 2, "priority": 1},
                    {"task_id": 10, "skill_id": 3, "priority": 0},
                ],
            )
            frozen = [{"id": 1, "instructions": "Frozen prompt", "version": 1}]
            connection.execute(
                jobs.insert(),
                [
                    dict(
                        id=1,
                        status="queued",
                        skill_snapshot=frozen,
                        request_snapshot={"skills": frozen, "skill_resolution": "manual_override"},
                    ),
                    dict(
                        id=2,
                        status="succeeded",
                        skill_snapshot=frozen,
                        request_snapshot={"skills": frozen},
                    ),
                ],
            )
            preserved = [skills, selections, jobs]
            before = [connection.execute(sa.select(table)).all() for table in preserved]
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
            assert [connection.execute(sa.select(table)).all() for table in preserved] == before
            assert connection.execute(
                sa.text("SELECT id, content_type FROM ai_skills ORDER BY id")
            ).all() == [(1, "prompt"), (2, "skill"), (3, "prompt"), (4, "skill")]
            assert "ix_ai_skills_content_type" in {
                item["name"] for item in sa.inspect(connection).get_indexes("ai_skills")
            }
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert "content_type" not in {
                item["name"] for item in sa.inspect(connection).get_columns("ai_skills")
            }
            assert [connection.execute(sa.select(table)).all() for table in preserved] == before
    finally:
        engine.dispose()
