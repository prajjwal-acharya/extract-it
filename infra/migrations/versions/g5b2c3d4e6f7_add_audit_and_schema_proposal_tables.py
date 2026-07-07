"""add persistence_audit_logs, schema_proposal_records, truth_audit_logs tables

Revision ID: g5b2c3d4e6f7
Revises: ab12cd34ef56
Create Date: 2026-07-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g5b2c3d4e6f7"
down_revision: Union[str, None] = "ab12cd34ef56"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "persistence_audit_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("resolution_strategy", sa.String(), nullable=True),
        sa.Column("resolution_reason", sa.Text(), nullable=True),
        sa.Column(
            "resolution_requires_human", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("learning_candidate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("allow_learning", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("learn_from_document", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("learn_from_correction", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("schema_candidate", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("learning_reason", sa.Text(), nullable=True),
        sa.Column("schema_proposal_json", sa.JSON(), nullable=True),
        sa.Column("persist_status", sa.String(), nullable=False),
        sa.Column("persist_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_persistence_audit_logs_document_id",
        "persistence_audit_logs",
        ["document_id"],
    )

    op.create_table(
        "schema_proposal_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=False),
        sa.Column("proposed_version", sa.String(), nullable=False),
        sa.Column("additions_json", sa.JSON(), nullable=False),
        sa.Column("relaxed_fields_json", sa.JSON(), nullable=False),
        sa.Column(
            "origin_document_id",
            sa.String(),
            sa.ForeignKey("documents.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_schema_proposal_records_doc_type",
        "schema_proposal_records",
        ["doc_type"],
    )

    op.create_table(
        "truth_audit_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("doc_type", sa.String(), nullable=True),
        sa.Column("final_confidence", sa.Float(), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=False),
        sa.Column("coverage_score", sa.Float(), nullable=False),
        sa.Column("required_fields_missing", sa.JSON(), nullable=False),
        sa.Column("additional_fields", sa.JSON(), nullable=False),
        sa.Column("verification_reports", sa.JSON(), nullable=False),
        sa.Column("document_status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("allow_completion", sa.Boolean(), nullable=False),
        sa.Column("allow_embedding", sa.Boolean(), nullable=False),
        sa.Column("allow_learning", sa.Boolean(), nullable=False),
        sa.Column("persistence_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("verifier_version", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_truth_audit_logs_document_id",
        "truth_audit_logs",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_truth_audit_logs_document_id", table_name="truth_audit_logs")
    op.drop_table("truth_audit_logs")

    op.drop_index("ix_schema_proposal_records_doc_type", table_name="schema_proposal_records")
    op.drop_table("schema_proposal_records")

    op.drop_index("ix_persistence_audit_logs_document_id", table_name="persistence_audit_logs")
    op.drop_table("persistence_audit_logs")
