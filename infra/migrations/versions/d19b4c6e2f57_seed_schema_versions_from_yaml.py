"""seed schema_versions from existing YAML files

Revision ID: d19b4c6e2f57
Revises: c7f2a9e1d834
Create Date: 2026-07-04 00:00:01.000000

"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
import yaml
from alembic import op

revision: str = "d19b4c6e2f57"
down_revision: Union[str, None] = "c7f2a9e1d834"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Path is relative to repo root at migration-run time (matches config/schema_loader.py's base).
_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "config" / "schemas"

_schema_versions = sa.table(
    "schema_versions",
    sa.column("id", sa.String),
    sa.column("doc_type", sa.String),
    sa.column("version", sa.String),
    sa.column("fields_json", sa.JSON),
    sa.column("universal_mapping_json", sa.JSON),
    sa.column("source", sa.String),
    sa.column("origin_document_id", sa.String),
    sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime),
)


def upgrade() -> None:
    if not _SCHEMA_DIR.exists():
        return  # defensive — CI's migrations job doesn't check out config/ in isolation

    rows = []
    for path in sorted(_SCHEMA_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "doc_type": raw["doc_type"],
                "version": str(raw.get("version", "1.0")),
                "fields_json": raw["fields"],
                "universal_mapping_json": raw.get("universal_mapping", {}),
                "source": "reference",
                "origin_document_id": None,
                "is_active": True,
                "created_at": datetime.utcnow(),
            }
        )

    if rows:
        op.bulk_insert(_schema_versions, rows)


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM schema_versions WHERE source = 'reference'"))
