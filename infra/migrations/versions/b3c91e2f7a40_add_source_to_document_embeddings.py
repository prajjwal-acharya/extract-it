"""add source to document_embeddings

Revision ID: b3c91e2f7a40
Revises: a0a44f40862e
Create Date: 2026-07-03 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c91e2f7a40"
down_revision: Union[str, None] = "a0a44f40862e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("document_embeddings", sa.Column("source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_embeddings", "source")
