"""create schema_versions table

Revision ID: c7f2a9e1d834
Revises: b3c91e2f7a40
Create Date: 2026-07-04 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7f2a9e1d834"
down_revision: Union[str, None] = "b3c91e2f7a40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "schema_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("universal_mapping_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("origin_document_id", sa.String(), sa.ForeignKey("documents.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("doc_type", "version", name="uq_schema_versions_doc_type_version"),
    )
    op.create_index(
        "one_active_per_doctype",
        "schema_versions",
        ["doc_type"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )


def downgrade() -> None:
    op.drop_index("one_active_per_doctype", table_name="schema_versions")
    op.drop_table("schema_versions")
