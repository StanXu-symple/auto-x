"""Persist article publish attempts for repeatable delivery history."""

import sqlalchemy as sa

from alembic import op

revision = "0021_article_publish_history"
down_revision = "0020_article_media_publishing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "article_publish_attempts",
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("article_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="queued", nullable=False),
        sa.Column("target_summary", sa.String(length=1000), nullable=False),
        sa.Column("delivery_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["article_id"], ["ai_drafts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("attempt_id"),
    )
    op.create_index(
        "ix_article_publish_attempts_article_id",
        "article_publish_attempts",
        ["article_id"],
    )
    op.create_index(
        "ix_article_publish_attempts_channel",
        "article_publish_attempts",
        ["channel"],
    )
    # Recover every historical QQ batch from its durable delivery rows.
    op.execute(
        sa.text(
            """
            INSERT INTO article_publish_attempts
                (attempt_id, article_id, channel, status, target_summary, delivery_count,
                 error, started_at, completed_at, created_at, updated_at)
            SELECT article_publish_attempt_id, article_id, 'qq',
                   CASE
                       WHEN COUNT(*) FILTER (WHERE status IN ('failed', 'cancelled')) > 0 THEN 'failed'
                       WHEN COUNT(*) FILTER (WHERE status <> 'sent') = 0 THEN 'published'
                       ELSE 'queued'
                   END,
                   MAX(bot_name) || ' · ' || COUNT(DISTINCT group_openid)::text || ' 个群',
                   COUNT(*), MAX(last_error), MIN(COALESCE(started_at, created_at)),
                   CASE
                       WHEN COUNT(*) FILTER (WHERE status IN ('failed', 'cancelled')) > 0
                            OR COUNT(*) FILTER (WHERE status <> 'sent') = 0
                       THEN MAX(completed_at)
                       ELSE NULL
                   END,
                   MIN(created_at), MAX(updated_at)
            FROM qq_deliveries
            WHERE kind = 'article' AND article_id IS NOT NULL
                  AND article_publish_attempt_id IS NOT NULL
            GROUP BY article_publish_attempt_id, article_id
            """
        )
    )
    # Older XHS attempts had no durable job row, so preserve the latest article result.
    op.execute(
        sa.text(
            """
            INSERT INTO article_publish_attempts
                (attempt_id, article_id, channel, status, target_summary, delivery_count,
                 error, started_at, completed_at, created_at, updated_at)
            SELECT publish_attempt_id, id, publish_channel, publish_status,
                   CASE WHEN publish_channel = 'qq' THEN '历史 QQ 推送' ELSE '当前小红书账号' END,
                   0, publish_error, updated_at,
                   CASE WHEN publish_status IN ('published', 'failed')
                        THEN COALESCE(published_at, updated_at) ELSE NULL END,
                   updated_at, updated_at
            FROM ai_drafts
            WHERE publish_attempt_id IS NOT NULL AND publish_channel IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM article_publish_attempts
                      WHERE article_publish_attempts.attempt_id = ai_drafts.publish_attempt_id
                  )
            """
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_article_publish_attempts_channel", table_name="article_publish_attempts"
    )
    op.drop_index(
        "ix_article_publish_attempts_article_id", table_name="article_publish_attempts"
    )
    op.drop_table("article_publish_attempts")
