"""Add the runtime-selectable X source provider setting.

Revision ID: 0005_x_source_provider
Revises: 0004_x_credentials
"""

import sqlalchemy as sa

from alembic import op

revision = "0005_x_source_provider"
down_revision = "0004_x_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO app_settings (`key`, value, updated_at)
            VALUES ('x_source', JSON_OBJECT('provider', 'official_api'), UTC_TIMESTAMP())
            ON DUPLICATE KEY UPDATE `key` = VALUES(`key`)
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM app_settings WHERE `key` = 'x_source'"))
