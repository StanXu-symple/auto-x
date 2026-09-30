"""Remember placeholder initialization so deleted built-ins stay deleted."""

from alembic import op

revision = "0028_qq_placeholder_seed_once"
down_revision = "0027_qq_placeholders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Revision 0027 already inserted the defaults for existing installations.
    op.execute(
        "INSERT INTO app_settings (key, value, updated_at) "
        "VALUES ('qq_placeholder_defaults_seeded', '{\"initialized\": true}', now()) "
        "ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key = 'qq_placeholder_defaults_seeded'")
