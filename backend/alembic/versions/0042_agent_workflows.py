"""Reusable agent nodes, ordered workflows and resumable executions."""

import sqlalchemy as sa

from alembic import op

revision = "0042_agent_workflows"
down_revision = "0041_agent_chat_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, start in (("ai_agent_nodes", 2000), ("ai_agent_workflows", 3000)):
        extra = (
            [
                sa.Column("node_type", sa.String(24), nullable=False, server_default="custom"),
                sa.Column("skill_ids", sa.JSON(), nullable=False, server_default="[]"),
            ]
            if table == "ai_agent_nodes"
            else []
        )
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), sa.Identity(start=start), primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text()),
            *extra,
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(f"ix_{table}_name", table, ["name"], unique=True)
        op.create_index(f"ix_{table}_is_active", table, ["is_active"])
    op.create_index("ix_ai_agent_nodes_node_type", "ai_agent_nodes", ["node_type"])
    op.create_table(
        "ai_agent_workflow_steps",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "workflow_id",
            sa.Integer(),
            sa.ForeignKey("ai_agent_workflows.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            sa.Integer(),
            sa.ForeignKey("ai_agent_nodes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workflow_id", "position", name="uq_ai_agent_workflow_steps_position"),
    )
    for col in ("workflow_id", "node_id"):
        op.create_index(f"ix_ai_agent_workflow_steps_{col}", "ai_agent_workflow_steps", [col])
    op.create_table(
        "agent_workflow_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("admins.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "workflow_id", sa.Integer(), sa.ForeignKey("ai_agent_workflows.id", ondelete="SET NULL")
        ),
        sa.Column("workflow_name", sa.String(100), nullable=False),
        sa.Column("workflow_version", sa.Integer(), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("source_snapshot", sa.JSON()),
        sa.Column("workflow_snapshot", sa.JSON(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_position", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.String(500)),
        sa.Column(
            "article_id", sa.BigInteger(), sa.ForeignKey("ai_drafts.id", ondelete="SET NULL")
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("admin_id", "request_id", name="uq_agent_workflow_run_request"),
    )
    for col in ("admin_id", "workflow_id"):
        op.create_index(f"ix_agent_workflow_runs_{col}", "agent_workflow_runs", [col])
    op.create_index(
        "ix_agent_workflow_runs_due", "agent_workflow_runs", ["status", "next_attempt_at"]
    )
    op.create_table(
        "agent_workflow_step_runs",
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("agent_workflow_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("node_name", sa.String(100), nullable=False),
        sa.Column("node_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("output", sa.JSON()),
        sa.Column("usage", sa.JSON()),
        sa.Column("error", sa.String(500)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    for table in (
        "agent_workflow_step_runs",
        "agent_workflow_runs",
        "ai_agent_workflow_steps",
        "ai_agent_workflows",
        "ai_agent_nodes",
    ):
        op.drop_table(table)
