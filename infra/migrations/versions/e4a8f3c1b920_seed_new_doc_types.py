"""seed gst_invoice, salary_slip, itr, property_deed into schema_versions

Revision ID: e4a8f3c1b920
Revises: d19b4c6e2f57
Create Date: 2026-07-04 00:00:02.000000

"""

import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4a8f3c1b920"
down_revision: Union[str, None] = "d19b4c6e2f57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_DOC_TYPES = [
    {
        "doc_type": "gst_invoice",
        "fields_json": [
            {"name": "seller_gstin", "type": "string", "required": True},
            {"name": "buyer_gstin", "type": "string", "required": False},
            {"name": "invoice_number", "type": "string", "required": True},
            {"name": "invoice_date", "type": "date", "format": "YYYY-MM-DD", "required": True},
            {"name": "seller_name", "type": "string", "required": True},
            {"name": "buyer_name", "type": "string", "required": False},
            {"name": "taxable_value", "type": "float", "required": True},
            {"name": "cgst", "type": "float", "required": False},
            {"name": "sgst", "type": "float", "required": False},
            {"name": "igst", "type": "float", "required": False},
            {"name": "total_amount", "type": "float", "required": True},
        ],
        "universal_mapping_json": {
            "holder_name": "seller_name",
            "id_number": "seller_gstin",
            "expiry_date": None,
        },
    },
    {
        "doc_type": "salary_slip",
        "fields_json": [
            {"name": "employee_name", "type": "string", "required": True},
            {"name": "employee_id", "type": "string", "required": True},
            {"name": "designation", "type": "string", "required": False},
            {"name": "pan_number", "type": "string", "required": False},
            {"name": "uan_number", "type": "string", "required": False},
            {"name": "pay_period", "type": "string", "required": True},
            {"name": "employer_name", "type": "string", "required": True},
            {"name": "basic_salary", "type": "float", "required": True},
            {"name": "hra", "type": "float", "required": False},
            {"name": "deductions", "type": "float", "required": False},
            {"name": "net_pay", "type": "float", "required": True},
        ],
        "universal_mapping_json": {
            "holder_name": "employee_name",
            "id_number": "employee_id",
            "expiry_date": None,
        },
    },
    {
        "doc_type": "itr",
        "fields_json": [
            {"name": "taxpayer_name", "type": "string", "required": True},
            {"name": "pan_number", "type": "string", "required": True},
            {"name": "assessment_year", "type": "string", "required": True},
            {"name": "filing_date", "type": "date", "format": "YYYY-MM-DD", "required": True},
            {"name": "acknowledgement_number", "type": "string", "required": True},
            {"name": "gross_total_income", "type": "float", "required": True},
            {"name": "total_tax_paid", "type": "float", "required": False},
            {"name": "refund_amount", "type": "float", "required": False},
        ],
        "universal_mapping_json": {
            "holder_name": "taxpayer_name",
            "id_number": "pan_number",
            "expiry_date": None,
        },
    },
    {
        "doc_type": "property_deed",
        "fields_json": [
            {"name": "executant_name", "type": "string", "required": True},
            {"name": "claimant_name", "type": "string", "required": True},
            {"name": "property_address", "type": "string", "required": True},
            {"name": "deed_number", "type": "string", "required": True},
            {
                "name": "registration_date",
                "type": "date",
                "format": "YYYY-MM-DD",
                "required": True,
            },
            {"name": "survey_number", "type": "string", "required": False},
            {"name": "sale_consideration", "type": "float", "required": True},
        ],
        "universal_mapping_json": {
            "holder_name": "claimant_name",
            "id_number": "deed_number",
            "expiry_date": None,
        },
    },
]

_NEW_DOC_TYPE_NAMES = [r["doc_type"] for r in _NEW_DOC_TYPES]

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
    bind = op.get_bind()
    now = datetime.utcnow()

    for row in _NEW_DOC_TYPES:
        # Idempotency: check in Python before inserting — avoids type-inference
        # ambiguity that arises when the same :param appears in both the SELECT
        # list and the WHERE clause of an INSERT...SELECT in psycopg.
        already_exists = bind.execute(
            sa.select(sa.func.count()).select_from(_schema_versions).where(
                sa.and_(
                    _schema_versions.c.doc_type == row["doc_type"],
                    _schema_versions.c.version == "1.0",
                )
            )
        ).scalar()

        if not already_exists:
            op.bulk_insert(
                _schema_versions,
                [
                    {
                        "id": str(uuid.uuid4()),
                        "doc_type": row["doc_type"],
                        "version": "1.0",
                        "fields_json": row["fields_json"],
                        "universal_mapping_json": row["universal_mapping_json"],
                        "source": "reference",
                        "origin_document_id": None,
                        "is_active": True,
                        "created_at": now,
                    }
                ],
            )


def downgrade() -> None:
    bind = op.get_bind()
    # Build the IN list with individual named params — safe since this is a hardcoded
    # constant, and avoids expanding-bindparam quirks with psycopg in alembic context.
    placeholders = ", ".join(f":dt{i}" for i in range(len(_NEW_DOC_TYPE_NAMES)))
    params = {f"dt{i}": name for i, name in enumerate(_NEW_DOC_TYPE_NAMES)}
    bind.execute(
        sa.text(
            f"DELETE FROM schema_versions WHERE doc_type IN ({placeholders})"
            " AND source = 'reference'"
        ),
        params,
    )
