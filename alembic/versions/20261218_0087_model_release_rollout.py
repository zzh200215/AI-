"""Versioned model routing rollout, shadow traffic and guardrail audit fields.

Revision ID: 20261218_0087
Revises: 20261217_0086
"""

import sqlalchemy as sa

from alembic import op


revision = "20261218_0087"
down_revision = "20261217_0086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_releases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False, server_default="*"),
        sa.Column("candidate_model", sa.String(length=128), nullable=False),
        sa.Column("candidate_provider", sa.String(length=64), nullable=True),
        sa.Column("candidate_base_url", sa.String(length=512), nullable=True),
        sa.Column("max_complexity", sa.String(length=16), nullable=False, server_default="complex"),
        sa.Column("max_risk_level", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("expected_latency_ms", sa.Integer(), nullable=True),
        sa.Column("max_cost_ratio", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("rollout_percentage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shadow_percentage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_sample_size", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("max_error_rate", sa.Float(), nullable=False, server_default="0.05"),
        sa.Column("max_p95_latency_ms", sa.Integer(), nullable=True),
        sa.Column("evaluation_status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("evaluation_gate_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
        sa.Column("previous_release_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("activated_by", sa.Integer(), nullable=True),
        sa.Column("rolled_back_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["rolled_back_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["previous_release_id"], ["model_releases.id"]),
        sa.UniqueConstraint("version", name="uq_model_releases_version"),
    )
    for column in ("version", "action", "evaluation_status", "status", "previous_release_id", "created_by", "activated_by", "rolled_back_by", "created_at", "activated_at"):
        op.create_index(f"ix_model_releases_{column}", "model_releases", [column])

    with op.batch_alter_table("llm_call_logs") as batch_op:
        batch_op.add_column(sa.Column("model_release_version", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("experiment_bucket", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("traffic_type", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("routing_reason", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("request_complexity", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("risk_level", sa.String(length=16), nullable=True))
        batch_op.create_index("ix_llm_call_logs_model_release_version", ["model_release_version"])
        batch_op.create_index("ix_llm_call_logs_traffic_type", ["traffic_type"])


def downgrade() -> None:
    with op.batch_alter_table("llm_call_logs") as batch_op:
        batch_op.drop_index("ix_llm_call_logs_traffic_type")
        batch_op.drop_index("ix_llm_call_logs_model_release_version")
        batch_op.drop_column("risk_level")
        batch_op.drop_column("request_complexity")
        batch_op.drop_column("routing_reason")
        batch_op.drop_column("traffic_type")
        batch_op.drop_column("experiment_bucket")
        batch_op.drop_column("model_release_version")
    for column in ("activated_at", "created_at", "rolled_back_by", "activated_by", "created_by", "previous_release_id", "status", "evaluation_status", "action", "version"):
        op.drop_index(f"ix_model_releases_{column}", table_name="model_releases")
    op.drop_table("model_releases")
