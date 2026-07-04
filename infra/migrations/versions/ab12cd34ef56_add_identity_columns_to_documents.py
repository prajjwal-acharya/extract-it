"""add hash, file_size, mime_type identity columns to documents

Revision ID: ab12cd34ef56
Revises: f3a1b2c4d5e6
Create Date: 2026-07-04 00:00:04.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "ab12cd34ef56"
down_revision = "f3a1b2c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("file_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("mime_type", sa.String(), nullable=True),
    )
    op.create_index(
        "uq_documents_hash",
        "documents",
        ["hash"],
        unique=True,
        postgresql_where=sa.text("hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_documents_hash", table_name="documents")
    op.drop_column("documents", "mime_type")
    op.drop_column("documents", "file_size")
    op.drop_column("documents", "hash")
