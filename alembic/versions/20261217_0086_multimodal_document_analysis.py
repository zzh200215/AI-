"""Versioned multimodal document layout/OCR evidence artifacts.

Revision ID: 20261217_0086
Revises: 20261216_0085
"""

import sqlalchemy as sa

from alembic import op


revision = "20261217_0086"
down_revision = "20261216_0085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_multimodal_analyses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ready"),
        sa.Column("parser_version", sa.String(64), nullable=True),
        sa.Column("vision_model", sa.String(128), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_multimodal_doc_version"),
    )
    op.create_index("ix_document_multimodal_analyses_document_id", "document_multimodal_analyses", ["document_id"])
    op.create_index("ix_document_multimodal_analyses_status", "document_multimodal_analyses", ["status"])


def downgrade() -> None:
    op.drop_index("ix_document_multimodal_analyses_status", table_name="document_multimodal_analyses")
    op.drop_index("ix_document_multimodal_analyses_document_id", table_name="document_multimodal_analyses")
    op.drop_table("document_multimodal_analyses")

