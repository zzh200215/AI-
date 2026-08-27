"""Review-gated Agent online evaluation candidates.

Revision ID: 20261215_0084
Revises: 20261201_0083
Create Date: 2026-12-15
"""
import sqlalchemy as sa

from alembic import op

revision = "20261215_0084"
down_revision = "20261201_0083"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_eval_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("agent_run_id", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("source_event_id", sa.Integer(), nullable=True),
        sa.Column("failure_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending_review"),
        sa.Column("goal_hash", sa.String(64), nullable=True),
        sa.Column("evidence_json", sa.Text(), nullable=True),
        sa.Column("evaluation_input_json", sa.Text(), nullable=True),
        sa.Column("expected_outcome_json", sa.Text(), nullable=True),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_event_id"], ["agent_audit_events.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("dedupe_key", name="uq_agent_eval_candidates_dedupe_key"),
    )
    for column in ("agent_run_id", "trace_id", "user_id", "organization_id", "source_event_id", "failure_type", "status"):
        op.create_index(f"ix_agent_eval_candidates_{column}", "agent_eval_candidates", [column])


def downgrade() -> None:
    for column in ("status", "failure_type", "source_event_id", "organization_id", "user_id", "trace_id", "agent_run_id"):
        op.drop_index(f"ix_agent_eval_candidates_{column}", table_name="agent_eval_candidates")
    op.drop_table("agent_eval_candidates")
