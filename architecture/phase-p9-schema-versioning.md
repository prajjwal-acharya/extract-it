# P9 — Schema Versioning

**Status:** ✅ Done  
**Scope:** schema_versions table, auto-discovery, version bumping, 4 new doc types

---

## What P9 delivered

P9 makes schemas a first-class runtime concept. Rather than hardcoding field lists
in YAML and redeploying when a new document variant appears, schemas are now versioned
in Postgres. The `schema_diff_agent` can discover new fields automatically during a
retry and create a new version without human intervention (though human approval is
required to activate it permanently).

P9 also expanded the supported document types from 3 to 7 (adding `salary_slip`,
`itr`, `gst_invoice`, `property_deed`).

---

## schema_versions table

`db/models.py` — `SchemaVersion`:

```python
class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    __table_args__ = (
        UniqueConstraint("doc_type", "version", name="uq_schema_versions_doc_type_version"),
        Index(
            "one_active_per_doctype",
            "doc_type",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: UUID PK
    doc_type: str                  # "passport", "bank_statement", ...
    version: str                   # "1.0", "1.1", ...
    fields_json: list[dict]        # list of field definitions
    universal_mapping_json: dict   # {holder_name: ..., id_number: ..., expiry_date: ...}
    source: str                    # "reference" | "auto_discovered"
    origin_document_id: UUID | None  # document that triggered auto-discovery
    is_active: bool
    created_at: datetime
```

The partial unique index `one_active_per_doctype` uses a `WHERE is_active` predicate.
This enforces that at most one row per `doc_type` has `is_active=TRUE`, at the Postgres
level — no application-level locking needed.

---

## Bootstrap: YAML → Postgres

`config/schemas/<doc_type>.yaml` files define the reference schemas. They are
**never mutated at runtime**.

On first deploy, Alembic migration `d19b4c6e2f57_seed_schema_versions_from_yaml.py`
reads every YAML file in `config/schemas/` and inserts one `SchemaVersion` row with
`version="1.0"`, `is_active=TRUE`, `source="reference"`.

---

## DB-first schema loading

`config/schema_loader.py` — `load_schema_model(doc_type)`:

```
load_schema_model(doc_type)
  ├── _load_active_row(session, doc_type)
  │     SELECT * FROM schema_versions WHERE doc_type=? AND is_active=TRUE
  │     → SchemaVersion row, or None
  ├── if found: use fields_json from DB row
  │   else:     _load_yaml_raw(doc_type) (YAML fallback)
  ├── _build_model(fields_json, doc_type, version)
  │     → dynamically builds Pydantic BaseModel subclass
  └── LRU cache keyed by "{doc_type}_{version}"
```

Cache is busted on version bump: a new version string produces a cache miss on
the next call.

Additional loader functions:
- `load_reference_fields(doc_type)` — returns `required_fields`, `optional_fields`
- `load_universal_mapping(doc_type)` — returns the `universal_mapping` dict
- `load_universal_mapping_fallback(doc_type)` — returns `universal_mapping_fallback`
  dict (always from YAML, not DB — used for secondary field resolution)

---

## Auto-discovery (schema_diff_agent)

During `op_a_retry`, `schema_diff_agent` discovers new fields and computes a diff:

```python
discovered = discover_fields(raw_bytes, mime_type)
# → {"account_iban": "GB29NWBK60161331926819", "sort_code": "60-16-13", ...}

diff = diff_schema(discovered, active_schema.fields_json)
# → SchemaDiff(
#       additions=["iban", "sort_code"],
#       relaxed_fields=["account_number"],  # required but absent in this doc
#   )

if diff.additions or diff.relaxed_fields:
    new_version = apply_diff(session, active_row, diff, document_id)
    # → new SchemaVersion(version="1.1", is_active=True, source="auto_discovered")
    # → old row: is_active=False
```

The next `load_schema_model` call picks up the new version (cache miss).

---

## Schema proposal workflow

Auto-discovered schema changes are staged as `SchemaProposalRecord` for human review:

```
Pipeline creates SchemaProposalRecord(status="pending")
        │
        ▼
GET /schema-proposals/pending  → reviewer sees the proposal
        │
        ├── POST /schema-proposals/{id}/approve
        │     → INSERT SchemaVersion (is_active=True)
        │     → UPDATE old SchemaVersion (is_active=False)
        │     → proposal.status = "approved"
        │
        └── POST /schema-proposals/{id}/reject
              → proposal.status = "rejected"
              → proposal.rejection_reason = reason
              (record never deleted — fully auditable)
```

---

## New document types added in P9

| doc_type | Fields | Verifiers |
|---|---|---|
| `salary_slip` | employee_name, PAN, UAN, employer, pay_period, basic_salary, allowances[], deductions[], net_pay, gross_salary | gross_consistency, pan_validation |
| `itr` | pan, assessment_year, financial_year, itr_form_type, gross_income, total_tax_paid, refund_amount, acknowledgement_number | pan_validation, ay_fy_consistency |
| `gst_invoice` | gstin, invoice_number, invoice_date, seller_name, buyer_name, hsn_code, taxable_value, cgst, sgst, igst, invoice_total | gstin_checksum, invoice_total_consistency |
| `property_deed` | deed_type, executant_name, claimant_name, property_description, area, consideration_amount, execution_date, registration_date | deed_date_consistency |

All four have YAML schemas in `config/schemas/` and entries in `pipelines/registry.py`.
Migration `e4a8f3c1b920_seed_new_doc_types.py` seeded their initial `SchemaVersion` rows.

---

## driving_license and aadhaar (added post-P9)

Two additional doc types were added after P9:

**driving_license:**
- Schema: `config/schemas/driving_license.yaml`
- Registry entry: added in `pipelines/registry.py`
- Migration: `i7d4e5f6g8h9_seed_driving_license_schema.py`
- No verifiers

**aadhaar:**
- Schema: `config/schemas/aadhaar.yaml`
- Fields: `aadhaar_number`, `full_name`, `date_of_birth`, `gender`, `address`, `vid`
- Registry entry: added in `pipelines/registry.py`
- Universal mapping: `holder_name → full_name`, `id_number → aadhaar_number`
- No verifiers

---

## Version bump mechanism

`apply_diff` in `agents/schema_diff_agent.py`:

```python
def apply_diff(session, active_row: SchemaVersion, diff: SchemaDiff, origin_document_id: str) -> SchemaVersion:
    # 1. Compute new version: "1.0" → "1.1"
    major, minor = active_row.version.split(".")
    new_version = f"{major}.{int(minor) + 1}"

    # 2. Merge additions into fields_json
    new_fields = list(active_row.fields_json) + [
        {"name": name, "type": "string", "required": False}
        for name in diff.additions
    ]

    # 3. Make required fields optional if they're in relaxed_fields
    for field in new_fields:
        if field["name"] in diff.relaxed_fields:
            field["required"] = False

    # 4. INSERT new SchemaVersion
    new_row = SchemaVersion(
        doc_type=active_row.doc_type,
        version=new_version,
        fields_json=new_fields,
        universal_mapping_json=active_row.universal_mapping_json,
        source="auto_discovered",
        origin_document_id=origin_document_id,
        is_active=True,
    )
    session.add(new_row)

    # 5. Deactivate old row
    active_row.is_active = False
    session.commit()
    return new_row
```
