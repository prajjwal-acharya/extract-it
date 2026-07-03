# Pre-P9 Fixes — GH Issues #5, #6 + ExtractionResult drop

## FIX-1: api/routes/review.py — auth + corrections validation

**Root cause**: `corrections: dict | None` had zero schema validation — any field
name reached `Command(resume=...)` unchecked. Route became live when `get_graph()` landed in P7.

**Changes**:
- `config/settings.py`: added `REVIEW_API_KEY: str = ""`
- `.env.example`: added `REVIEW_API_KEY=` placeholder
- `api/routes/review.py`: added `APIKeyHeader` dependency (`_require_api_key`);
  open when `REVIEW_API_KEY` is unset (dev-mode safe). Added `graph.get_state()`
  check → 404 if no pending review. Added corrections field validation against
  `load_schema_model(doc_type)` → 422 on unknown fields.

**Live verification**: `POST /review/nonexistent-thread/decision` → 404. 
`POST /review/{completed-id}/decision {"corrections": {"bad_field": "value"}}` → 422.

## FIX-2: pipelines/nodes/normalize.py — date canonicalization

**Root cause**: `_resolve` was pure string passthrough. Live runs stored
`"10/02/2020"` and `"09 JAN 2030"` — mixed formats break query-layer date
comparisons.

**Critical edge case found during implementation**: `dayfirst=True` breaks
already-ISO dates — `"2030-02-10"` would be reinterpreted as Oct 2 (02=day,
10=month). Fixed with `_ISO_DATE` regex: skip `dayfirst` for YYYY-MM-DD inputs.

**Changes**:
- `pyproject.toml`: added `python-dateutil>=2.9.0`
- `pipelines/nodes/normalize.py`: added `_canonicalize_date()` applied only to
  `expiry_date` after `_resolve` (not inside it, not applied to holder_name/id_number)

## FIX-3: ExtractionResult — DROP

**Root cause**: defined in `db/models.py` + initial migration, zero writes anywhere.
`ConfidenceLog` covers the retry audit trail.

**Changes**:
- `db/models.py`: removed `ExtractionResult` class + `extraction_results`
  relationship from `Document`
- `infra/migrations/versions/a0a44f40862e_drop_extraction_results_table.py`:
  new migration — `upgrade()` drops `extraction_results` only (autogenerate
  wanted to also drop checkpointer tables; hand-edited to scope it correctly)
- `tests/unit/test_db.py`: removed `test_extraction_result_model_columns_exist` stub
- Zero remaining references: `grep -rn ExtractionResult` returns nothing outside
  the migration file

**Migration verification**: `\dt` after `make migrate` shows 8 tables
(alembic_version, 3 app tables, 4 LangGraph checkpointer tables) — `extraction_results` absent.

## Regression tests added

| Test | File | Verifies |
|---|---|---|
| `test_decision_rejects_unknown_correction_fields` | test_review.py | 422 on bad field |
| `test_decision_returns_404_for_unknown_thread_id` | test_review.py | 404 on missing thread |
| `test_decision_requires_api_key_when_configured` | test_review.py | 401 without header |
| `test_decision_passes_with_correct_api_key` | test_review.py | 200 with header |
| `test_decision_allows_valid_correction_fields` | test_review.py | 200 on valid field |
| `test_normalize_canonicalizes_expiry_date` | test_pipelines.py | ISO+slash+verbose formats |
| `test_normalize_leaves_unparseable_date_unchanged` | test_pipelines.py | safe fallback |

## Gate

```
78 passed (+7 from P8 baseline of 71), 15 pre-existing stubs, ruff clean, mypy clean
```
