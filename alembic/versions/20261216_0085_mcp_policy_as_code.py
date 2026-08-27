"""Versioned MCP Policy-as-Code documents.

Revision ID: 20261216_0085
Revises: 20261215_0084
Create Date: 2026-12-16
"""
import sqlalchemy as sa

from alembic import op

revision = "20261216_0085"
down_revision = "20261215_0084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_policy_versions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("policy_json", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("activated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["activated_by"], ["users.id"]),
        sa.UniqueConstraint("version", name="uq_mcp_policy_versions_version"),
    )
    op.create_index("ix_mcp_policy_versions_version", "mcp_policy_versions", ["version"])
    op.create_index("ix_mcp_policy_versions_status", "mcp_policy_versions", ["status"])

    with op.batch_alter_table("agent_approval_requests") as batch:
        batch.add_column(sa.Column("policy_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("data_scope", sa.String(64), nullable=True))
        batch.create_index("ix_agent_approval_requests_policy_version", ["policy_version"])


def downgrade() -> None:
    with op.batch_alter_table("agent_approval_requests") as batch:
        batch.drop_index("ix_agent_approval_requests_policy_version")
        batch.drop_column("data_scope")
        batch.drop_column("policy_version")
    op.drop_index("ix_mcp_policy_versions_status", table_name="mcp_policy_versions")
    op.drop_index("ix_mcp_policy_versions_version", table_name="mcp_policy_versions")
    op.drop_table("mcp_policy_versions")
