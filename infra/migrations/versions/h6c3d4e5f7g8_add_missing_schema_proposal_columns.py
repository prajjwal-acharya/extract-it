"""add approved_schema_version and updated_at to schema_proposal_records

Revision ID: h6c3d4e5f7g8
Revises: g5b2c3d4e6f7
Create Date: 2026-07-05 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h6c3d4e5f7g8"
down_revision: Union[str, None] = "g5b2c3d4e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schema_proposal_records",
        sa.Column("approved_schema_version", sa.String(), nullable=True),
    )
    op.add_column(
        "schema_proposal_records",
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("schema_proposal_records", "updated_at")
    op.drop_column("schema_proposal_records", "approved_schema_version")
