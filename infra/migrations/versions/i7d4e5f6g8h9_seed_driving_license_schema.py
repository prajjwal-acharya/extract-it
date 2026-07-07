"""seed driving_license into schema_versions

Revision ID: i7d4e5f6g8h9
Revises: h6c3d4e5f7g8
Create Date: 2026-07-05 00:00:02.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "i7d4e5f6g8h9"
down_revision: Union[str, None] = "h6c3d4e5f7g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # driving_license.yaml is now picked up by d19b4c6e2f57 which globs all YAMLs.
    # This migration is a no-op kept only to preserve the chain.
    pass


def downgrade() -> None:
    pass
