"""Internal A2A delegation lineage and auditable control-plane ledger.

Revision ID: 20261219_0088
Revises: 20261218_0087
"""

import sqlalchemy as sa

from alembic import op

revision = "20261219_0088"
down_revision = "20261218_0087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.add_column(sa.Column("agent_type", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("parent_run_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("delegation_id", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key("fk_agent_runs_parent_run_id", "agent_runs", ["parent_run_id"], ["id"])
        batch_op.create_index("ix_agent_runs_agent_type", ["agent_type"])
        batch_op.create_index("ix_agent_runs_parent_run_id", ["parent_run_id"])
        batch_op.create_index("ix_agent_runs_delegation_id", ["delegation_id"], unique=True)

    op.create_table(
        "a2a_delegations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("delegation_id", sa.String(length=64), nullable=False),
        sa.Column("parent_run_id", sa.Integer(), nullable=False),
        sa.Column("child_run_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("authorization_snapshot_id", sa.String(length=64), nullable=True),
        sa.Column("from_agent_type", sa.String(length=64), nullable=False),
        sa.Column("to_agent_type", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False, server_default="analysis"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="accepted"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("task_summary_json", sa.Text(), nullable=True),
        sa.Column("result_summary_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["child_run_id"], ["agent_runs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint("delegation_id", name="uq_a2a_delegations_delegation_id"),
        sa.UniqueConstraint("child_run_id", name="uq_a2a_delegations_child_run_id"),
        sa.UniqueConstraint("parent_run_id", "idempotency_key", name="uq_a2a_delegations_parent_key"),
    )
    for column in (
        "delegation_id", "parent_run_id", "child_run_id", "user_id", "organization_id",
        "trace_id", "authorization_snapshot_id", "from_agent_type", "to_agent_type",
        "status", "input_hash", "created_at",
    ):
        op.create_index(f"ix_a2a_delegations_{column}", "a2a_delegations", [column])


def downgrade() -> None:
    for column in (
        "created_at", "input_hash", "status", "to_agent_type", "from_agent_type",
        "authorization_snapshot_id", "trace_id", "organization_id", "user_id", "child_run_id",
        "parent_run_id", "delegation_id",
    ):
        op.drop_index(f"ix_a2a_delegations_{column}", table_name="a2a_delegations")
    op.drop_table("a2a_delegations")

    with op.batch_alter_table("agent_runs") as batch_op:
        batch_op.drop_index("ix_agent_runs_delegation_id")
        batch_op.drop_index("ix_agent_runs_parent_run_id")
        batch_op.drop_index("ix_agent_runs_agent_type")
        batch_op.drop_constraint("fk_agent_runs_parent_run_id", type_="foreignkey")
        batch_op.drop_column("delegation_id")
        batch_op.drop_column("parent_run_id")
        batch_op.drop_column("agent_type")
