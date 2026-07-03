"""drop extraction_results table

Revision ID: a0a44f40862e
Revises: da5070439f01
Create Date: 2026-07-03 13:31:58.068772

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a0a44f40862e'
down_revision: Union[str, None] = 'da5070439f01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('extraction_results')


def downgrade() -> None:
    op.create_table(
        'extraction_results',
        sa.Column('id', sa.VARCHAR(), nullable=False),
        sa.Column('document_id', sa.VARCHAR(), nullable=False),
        sa.Column('agent', sa.VARCHAR(), nullable=False),
        sa.Column('attempt', sa.INTEGER(), nullable=False),
        sa.Column('raw_output', sa.JSON(), nullable=True),
        sa.Column('confidence', sa.DOUBLE_PRECISION(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id'),
    )
