"""Classify collected tweets and configure group target listening modes."""

import sqlalchemy as sa

from alembic import op

revision = "0029_tweet_type_target_mode"
down_revision = "0028_qq_placeholder_seed_once"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tweets", sa.Column("tweet_type", sa.String(16), nullable=False, server_default="original")
    )
    op.add_column(
        "qq_notification_targets",
        sa.Column("listen_mode", sa.String(16), nullable=False, server_default="all"),
    )
    # Both providers persist normalized references. Fall back to raw data for older rows.
    op.execute("""
        WITH classified AS (
            SELECT id, CASE
                WHEN refs @> '[{"type":"retweeted"}]'::jsonb THEN 'retweet'
                WHEN refs @> '[{"type":"replied_to"}]'::jsonb THEN 'reply'
                WHEN refs @> '[{"type":"quoted"}]'::jsonb THEN 'retweet'
                ELSE 'original' END AS kind
            FROM (
                SELECT id, CASE
                    WHEN jsonb_typeof(referenced_tweets::jsonb) = 'array'
                        THEN referenced_tweets::jsonb
                    WHEN jsonb_typeof(raw_payload::jsonb -> 'referenced_tweets') = 'array'
                        THEN raw_payload::jsonb -> 'referenced_tweets'
                    ELSE '[]'::jsonb END AS refs
                FROM tweets
            ) source
        )
        UPDATE tweets SET tweet_type = classified.kind FROM classified
        WHERE tweets.id = classified.id AND tweets.tweet_type <> classified.kind
    """)
    op.create_index("ix_tweets_tweet_type", "tweets", ["tweet_type"])


def downgrade() -> None:
    op.drop_index("ix_tweets_tweet_type", table_name="tweets")
    op.drop_column("tweets", "tweet_type")
    op.drop_column("qq_notification_targets", "listen_mode")
