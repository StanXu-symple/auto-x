"""Add AI generation settings, skills, jobs, and drafts.

Revision ID: 0003_ai_creation
Revises: 0002_polling_fencing
Create Date: 2026-08-31

Only MySQL 5.7-compatible table/index/foreign-key operations are used. In
particular, this migration avoids CHECK constraints, generated columns, JSON
defaults, and MySQL 8-only DDL.
"""

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0003_ai_creation"
down_revision = "0002_polling_fencing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_skills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("remote_skill_id", sa.String(128), nullable=True),
        sa.Column("remote_skill_version", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_skills"),
        sa.UniqueConstraint("name", name="uq_ai_skills_name"),
    )
    op.create_index("ix_ai_skills_name", "ai_skills", ["name"])
    op.create_index("ix_ai_skills_is_active", "ai_skills", ["is_active"])

    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("auto_generate", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("provider", sa.String(32), server_default="openai_responses", nullable=False),
        sa.Column("model", sa.String(128), server_default="gpt-5.6-terra", nullable=False),
        sa.Column(
            "base_url",
            sa.String(500),
            server_default="https://api.openai.com/v1",
            nullable=False,
        ),
        sa.Column("bridge_url", sa.String(500), nullable=True),
        sa.Column("prompt_template", sa.Text(), nullable=True),
        sa.Column("language", sa.String(32), server_default="zh-CN", nullable=False),
        sa.Column("tone", sa.String(64), server_default="专业自然", nullable=False),
        sa.Column("require_review", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("reasoning_effort", sa.String(16), server_default="medium", nullable=False),
        sa.Column("default_skill_ids", sa.JSON(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), server_default="2500", nullable=False),
        sa.Column("request_timeout_seconds", sa.Integer(), server_default="60", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_ai_settings"),
    )

    op.create_table(
        "ai_generation_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_tweet_id", sa.BigInteger(), nullable=False),
        sa.Column("skill_id", sa.Integer(), nullable=True),
        sa.Column("skill_ids", sa.JSON(), nullable=False),
        sa.Column("skill_snapshot", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(191), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(36), nullable=True),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("manual", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("request_snapshot", sa.JSON(), nullable=True),
        sa.Column("response_snapshot", sa.JSON(), nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("source_text_hash", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_tweet_id"],
            ["tweets.id"],
            name="fk_ai_generation_jobs_source_tweet_id_tweets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_id"],
            ["ai_skills.id"],
            name="fk_ai_generation_jobs_skill_id_ai_skills",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_generation_jobs"),
        sa.UniqueConstraint("idempotency_key", name="uq_ai_generation_jobs_idempotency_key"),
    )
    op.create_index(
        "ix_ai_generation_jobs_source_tweet_id",
        "ai_generation_jobs",
        ["source_tweet_id"],
    )
    op.create_index("ix_ai_generation_jobs_skill_id", "ai_generation_jobs", ["skill_id"])
    op.create_index(
        "ix_ai_generation_jobs_due",
        "ai_generation_jobs",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_ai_generation_jobs_tweet_created",
        "ai_generation_jobs",
        ["source_tweet_id", "created_at"],
    )

    op.create_table(
        "ai_drafts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("source_tweet_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), server_default="draft", nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["ai_generation_jobs.id"],
            name="fk_ai_drafts_job_id_ai_generation_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_tweet_id"],
            ["tweets.id"],
            name="fk_ai_drafts_source_tweet_id_tweets",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_drafts"),
        sa.UniqueConstraint("job_id", name="uq_ai_drafts_job_id"),
    )
    op.create_index("ix_ai_drafts_job_id", "ai_drafts", ["job_id"])
    op.create_index("ix_ai_drafts_source_tweet_id", "ai_drafts", ["source_tweet_id"])

    # Seed editable, local instruction sets. These IDs are safe because the table
    # was created immediately above and is empty in this migration.
    now = datetime.now(UTC).replace(tzinfo=None)
    skill_table = sa.table(
        "ai_skills",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("instructions", sa.Text()),
        sa.column("output_schema", sa.JSON()),
        sa.column("is_active", sa.Boolean()),
        sa.column("version", sa.Integer()),
        sa.column("remote_skill_id", sa.String()),
        sa.column("remote_skill_version", sa.String()),
        sa.column("created_at", sa.DateTime()),
        sa.column("updated_at", sa.DateTime()),
    )
    op.bulk_insert(
        skill_table,
        [
            {
                "id": 1,
                "name": "观点提炼",
                "description": "从原帖提炼核心观点，生成可继续编辑的中文草稿。",
                "instructions": (
                    "准确提炼原帖的核心观点与依据，保留不确定性，不虚构事实。"
                    "输出适合中文读者的独立草稿，并明确区分原帖事实与作者评论。"
                ),
                "output_schema": None,
                "is_active": True,
                "version": 1,
                "remote_skill_id": None,
                "remote_skill_version": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": 2,
                "name": "中文短帖",
                "description": "将原帖改写为简洁自然的中文社交媒体草稿。",
                "instructions": (
                    "将信息改写为自然、简洁、有上下文的中文短帖。"
                    "避免夸大、诱导互动和未经来源支持的断言。"
                ),
                "output_schema": None,
                "is_active": True,
                "version": 1,
                "remote_skill_id": None,
                "remote_skill_version": None,
                "created_at": now,
                "updated_at": now,
            },
            {
                "id": 3,
                "name": "线程拆分",
                "description": "把复杂原帖拆解为结构清晰的线程草稿。",
                "instructions": (
                    "把原帖内容拆成由浅入深的短段落，第一段说明价值，后续逐点展开。"
                    "不要补造材料；在线程编号和段落边界上保持清晰。"
                ),
                "output_schema": None,
                "is_active": True,
                "version": 1,
                "remote_skill_id": None,
                "remote_skill_version": None,
                "created_at": now,
                "updated_at": now,
            },
        ],
    )
    # Alembic's offline literal renderer cannot serialize JSON values. JSON_ARRAY is
    # available in MySQL 5.7 and keeps `alembic upgrade --sql` usable.
    op.execute(
        sa.text(
            "INSERT INTO ai_settings "
            "(id, enabled, auto_generate, provider, model, base_url, bridge_url, "
            "prompt_template, language, tone, require_review, reasoning_effort, "
            "default_skill_ids, max_attempts, max_output_tokens, "
            "request_timeout_seconds, updated_at) VALUES "
            "(1, 0, 1, 'openai_responses', 'gpt-5.6-terra', "
            "'https://api.openai.com/v1', NULL, NULL, 'zh-CN', '专业自然', 1, "
            "'medium', JSON_ARRAY(1), 3, 2500, 60, UTC_TIMESTAMP())"
        )
    )


def downgrade() -> None:
    op.drop_table("ai_drafts")
    op.drop_table("ai_generation_jobs")
    op.drop_table("ai_settings")
    op.drop_table("ai_skills")
