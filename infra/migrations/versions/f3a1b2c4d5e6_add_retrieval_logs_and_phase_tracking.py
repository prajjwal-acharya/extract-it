"""add retrieval_logs table, current_phase + extracted_fields columns

Revision ID: f3a1b2c4d5e6
Revises: e4a8f3c1b920
Create Date: 2026-07-04 00:00:03.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f3a1b2c4d5e6"
down_revision = "e4a8f3c1b920"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("current_phase", sa.String(), server_default="pending", nullable=False),
    )
    op.add_column(
        "documents",
        sa.Column("extracted_fields", sa.JSON(), nullable=True),
    )
    op.create_table(
        "retrieval_logs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column(
            "retrieved_document_id",
            sa.String(),
            sa.ForeignKey("documents.id"),
            nullable=False,
        ),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("retrieval_logs")
    op.drop_column("documents", "extracted_fields")
    op.drop_column("documents", "current_phase")
