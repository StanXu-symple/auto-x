"""Store encrypted Xiaohongshu credentials per administrator."""

import sqlalchemy as sa

from alembic import op

revision = "0018_xhs_user_credentials"
down_revision = "0017_qq_template_variables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("xiaohongshu_credentials"):
        return
    op.create_table(
        "xiaohongshu_credentials",
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_a1", sa.Text(), nullable=False),
        sa.Column("encrypted_web_session", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admins.id"],
            name="fk_xiaohongshu_credentials_admin_id_admins",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("admin_id", name="pk_xiaohongshu_credentials"),
    )


def downgrade() -> None:
    op.drop_table("xiaohongshu_credentials")
