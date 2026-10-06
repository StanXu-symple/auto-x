"""Persist AI listening tasks, backfills, jobs and append-only attempts."""

import sqlalchemy as sa

from alembic import op

revision = "0032_ai_listen_tasks"
down_revision = "0031_tweet_screenshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("monitored_users", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column(
        "ai_settings",
        sa.Column(
            "auto_trigger_mode", sa.String(24), nullable=False, server_default="listening_tasks"
        ),
    )
    # Existing installations retain their prior implicit auto-generation behavior.
    op.execute("UPDATE ai_settings SET auto_trigger_mode = 'legacy_all'")
    op.create_table(
        "ai_listen_tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("desired_state", sa.String(16), nullable=False, server_default="paused"),
        sa.Column("queue_hold_reason", sa.String(64)),
        sa.Column("all_monitored_users", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("listen_mode", sa.String(16), nullable=False, server_default="original"),
        sa.Column(
            "feature_code", sa.String(64), nullable=False, server_default="article_generation"
        ),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("initial_sync_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("initial_backfill_from", sa.DateTime(timezone=True)),
        sa.Column("initial_backfill_to", sa.DateTime(timezone=True)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("max_attempts_override", sa.Integer()),
        sa.Column("language_override", sa.String(32)),
        sa.Column("tone_override", sa.String(64)),
        sa.Column("max_output_tokens_override", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_listen_tasks_desired_state", "ai_listen_tasks", ["desired_state"])
    op.create_table(
        "ai_listen_task_subscriptions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("ai_listen_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "monitored_user_id",
            sa.Integer(),
            sa.ForeignKey("monitored_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "task_id", "monitored_user_id", name="uq_ai_listen_task_subscriptions_task_id"
        ),
    )
    op.create_index(
        "ix_ai_listen_task_subscriptions_task_id", "ai_listen_task_subscriptions", ["task_id"]
    )
    op.create_index(
        "ix_ai_listen_task_subscriptions_monitored_user_id",
        "ai_listen_task_subscriptions",
        ["monitored_user_id"],
    )
    op.create_table(
        "ai_listen_task_skills",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("ai_listen_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_id",
            sa.Integer(),
            sa.ForeignKey("ai_skills.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.UniqueConstraint("task_id", "skill_id", name="uq_ai_listen_task_skills_task_id"),
    )
    op.create_index("ix_ai_listen_task_skills_task_id", "ai_listen_task_skills", ["task_id"])
    op.create_index("ix_ai_listen_task_skills_skill_id", "ai_listen_task_skills", ["skill_id"])
    op.create_table(
        "ai_listen_task_backfills",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("ai_listen_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(120), nullable=False),
        sa.Column("from_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("to_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("cursor_posted_at", sa.DateTime(timezone=True)),
        sa.Column("cursor_tweet_id", sa.BigInteger()),
        sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enqueued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "request_id", name="uq_ai_listen_task_backfills_task_id"),
    )
    op.create_index("ix_ai_listen_task_backfills_task_id", "ai_listen_task_backfills", ["task_id"])
    op.add_column("ai_generation_jobs", sa.Column("listen_task_id", sa.BigInteger()))
    op.add_column(
        "ai_generation_jobs",
        sa.Column("trigger_type", sa.String(24), nullable=False, server_default="legacy_auto"),
    )
    op.add_column("ai_generation_jobs", sa.Column("task_snapshot", sa.JSON()))
    op.add_column("ai_generation_jobs", sa.Column("task_config_version", sa.Integer()))
    op.add_column(
        "ai_generation_jobs",
        sa.Column("lifetime_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ai_generation_jobs",
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_foreign_key(
        "fk_ai_generation_jobs_listen_task_id_ai_listen_tasks",
        "ai_generation_jobs",
        "ai_listen_tasks",
        ["listen_task_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_ai_job_listen_task_tweet", "ai_generation_jobs", ["listen_task_id", "source_tweet_id"]
    )
    op.create_index(
        "ix_ai_generation_jobs_listen_task_id", "ai_generation_jobs", ["listen_task_id"]
    )
    op.create_index("ix_ai_generation_jobs_trigger_type", "ai_generation_jobs", ["trigger_type"])
    op.execute(
        "UPDATE ai_generation_jobs "
        "SET trigger_type = CASE WHEN manual THEN 'manual' ELSE 'legacy_auto' END, "
        "lifetime_attempts = attempts"
    )
    op.create_table(
        "ai_listen_task_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("ai_listen_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column(
            "job_id", sa.BigInteger(), sa.ForeignKey("ai_generation_jobs.id", ondelete="SET NULL")
        ),
        sa.Column(
            "backfill_id",
            sa.BigInteger(),
            sa.ForeignKey("ai_listen_task_backfills.id", ondelete="SET NULL"),
        ),
        sa.Column("summary", sa.String(500), nullable=False),
        sa.Column("details", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ai_listen_task_events_task_id", "ai_listen_task_events", ["task_id"])
    op.create_index("ix_ai_listen_task_events_event_type", "ai_listen_task_events", ["event_type"])
    op.create_table(
        "ai_generation_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "job_id",
            sa.BigInteger(),
            sa.ForeignKey("ai_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lifetime_number", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(24), nullable=False, server_default="running"),
        sa.Column("error_type", sa.String(100)),
        sa.Column("error_summary", sa.Text()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("provider", sa.String(64)),
        sa.Column("model_name", sa.String(128)),
        sa.Column("data_source_name", sa.String(128)),
        sa.Column("data_source_version", sa.Integer()),
        sa.UniqueConstraint("job_id", "lifetime_number", name="uq_ai_generation_attempts_job_id"),
    )
    op.create_index("ix_ai_generation_attempts_job_id", "ai_generation_attempts", ["job_id"])


def downgrade() -> None:
    op.drop_table("ai_generation_attempts")
    op.drop_table("ai_listen_task_events")
    op.drop_index("ix_ai_generation_jobs_trigger_type", table_name="ai_generation_jobs")
    op.drop_index("ix_ai_generation_jobs_listen_task_id", table_name="ai_generation_jobs")
    op.drop_constraint("uq_ai_job_listen_task_tweet", "ai_generation_jobs", type_="unique")
    op.drop_constraint(
        "fk_ai_generation_jobs_listen_task_id_ai_listen_tasks",
        "ai_generation_jobs",
        type_="foreignkey",
    )
    for name in (
        "is_archived",
        "lifetime_attempts",
        "task_config_version",
        "task_snapshot",
        "trigger_type",
        "listen_task_id",
    ):
        op.drop_column("ai_generation_jobs", name)
    op.drop_table("ai_listen_task_backfills")
    op.drop_table("ai_listen_task_skills")
    op.drop_table("ai_listen_task_subscriptions")
    op.drop_table("ai_listen_tasks")
    op.drop_column("ai_settings", "auto_trigger_mode")
    op.drop_column("monitored_users", "archived_at")
