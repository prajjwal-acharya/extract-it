# P10 — Schema versioning, auto-discovery, 4 new doc_types

## Deliverables shipped

| Commit | File(s) | Change |
|---|---|---|
| `c5f13ee` | `db/models.py` | New `SchemaVersion` ORM model; partial unique index (`one_active_per_doctype WHERE is_active`) |
| `c5f13ee` | `infra/migrations/versions/c7f2a9e1d834_*` | Create `schema_versions` table + partial unique index |
| `c5f13ee` | `infra/migrations/versions/d19b4c6e2f57_*` | Seed `passport` + `bank_statement` into `schema_versions` v1.0 from YAML |
| `c5f13ee` | `config/schema_loader.py` | DB-first lookup (`schema_versions`) with YAML fallback; cache key = version string (auto-invalidates on bump) |
| `c5f13ee` | `agents/schema_diff_agent.py` | New: `discover_fields()`, `diff_schema()`, `apply_diff()`, `normalize_key()`, `_bump_version()` |
| `c5f13ee` | `pipelines/nodes/op_a_retry.py` | Run `_run_schema_discovery()` before RAG+extract; best-effort (never blocks extraction) |
| `c5f13ee` | `pipelines/state.py` | Added `schema_version: str \| None` field |
| `c5f13ee` | `io_pipeline/output_writer.py` | Log active schema version to `ConfidenceLog` |
| `2162eac` | `agents/schema_diff_agent.py` | Fix: skip `type: array` fields from relaxation check in `diff_schema()` + `apply_diff()` |
| `2162eac` | `tests/unit/test_schema_diff_agent.py` | 17 unit tests covering all public functions |
| `82cb10c` | `config/schemas/gst_invoice.yaml` | New schema: 11 fields |
| `82cb10c` | `config/schemas/salary_slip.yaml` | New schema: 11 fields |
| `82cb10c` | `config/schemas/itr.yaml` | New schema: 8 fields |
| `82cb10c` | `config/schemas/property_deed.yaml` | New schema: 7 fields |
| `82cb10c` | `infra/migrations/versions/e4a8f3c1b920_*` | Seed 4 new doc_types into `schema_versions` v1.0; idempotent (Python-side existence check before `bulk_insert`) |

## Architecture: schema versioning system

```
config/schemas/<doc_type>.yaml   ← static bootstrap (never mutated at runtime)
         │ seeded by migration d19b4c6e2f57 / e4a8f3c1b920
         ▼
schema_versions (Postgres)       ← source of truth once seeded
  doc_type | version | fields_json | is_active
  ─────────────────────────────────────────────
  passport  | 1.0    | [...]       | TRUE   ← partial unique index: only one
  passport  | 1.1    | [...]       | FALSE     active row per doc_type
         │
         │ schema_diff_agent (on op_a_retry)
         ▼
  discover_fields() → Gemini free-form extraction (no response_schema)
         │
  diff_schema()     → fuzzy-match (SequenceMatcher ≥ 0.82)
         │             scalar fields only — array fields excluded
         │             additions: genuinely new labels
         │             relaxed_fields: required fields absent from discovery
         ▼
  apply_diff()      → deactivate old row → flush → insert new row (is_active=TRUE)
                      version bumped: "1.0" → "1.1"
```

### Key design decisions

**DB-first, YAML fallback**: `load_schema_model()` and `load_universal_mapping()` query `schema_versions` first. If no DB row exists (test environments, fresh installs before migration), they fall back to the YAML file. Cache key includes the version string so a bump auto-invalidates without mtime tracking.

**Array fields excluded from diff**: `transactions` in `bank_statement.yaml` is `type: array` with nested `items`. Flat discovery can't surface transaction rows as key-value pairs, so array-type fields are excluded from both fuzzy matching and relaxation. Nested item-level diffing is explicitly deferred.

**Atomic version flip**: `apply_diff()` sets `active_row.is_active = False` + `session.flush()` before inserting the new active row — the partial unique index would otherwise see two active rows momentarily.

**Best-effort discovery**: `_run_schema_discovery()` wraps everything in try/except. A Gemini failure, malformed JSON, or any DB error logs a warning and returns `None` — extraction proceeds against the current active schema regardless.

**Idempotent seed migration**: `e4a8f3c1b920` checks existence in Python before `bulk_insert()` — avoids `uq_schema_versions_doc_type_version` violations on the CI round-trip (`upgrade → downgrade → upgrade`). Raw `INSERT WHERE NOT EXISTS` was attempted first but hit psycopg type-inference bugs (`:param::jsonb` cast syntax and same-param reuse in SELECT + WHERE), so Python-side check was used instead.

## Supported doc_types (post P10)

| doc_type | Fields | Schema version |
|---|---|---|
| `passport` | 11 | 1.0 |
| `bank_statement` | 8 scalar + 1 array (transactions) | 1.0 |
| `gst_invoice` | 11 | 1.0 |
| `salary_slip` | 11 | 1.0 |
| `itr` | 8 | 1.0 |
| `property_deed` | 7 | 1.0 |

## Test results

```
17 passed  tests/unit/test_schema_diff_agent.py
ruff clean, mypy clean (--ignore-missing-imports)
Migration round-trip verified locally: pgvector/pgvector:pg16
load_schema_model() resolves all 4 new doc_types from DB (not YAML fallback)
```

## Known limitation (explicitly deferred)

Nested/array field schema evolution is unsupported. Only top-level scalar fields participate in discovery, diff, and relaxation. Diffing into `transactions` item sub-schemas requires a separate design decision (per-document transaction schema variance) and is out of scope for this pass.
