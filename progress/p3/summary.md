# P3 — Extract Agent + Schema Loader

**Status:** Complete ✅
**Branch:** `main`

---

## What this phase covers

YAML schema loading (with mtime-based hot-reload cache), dynamic Pydantic model generation
from schema fields, extraction agent (multimodal bytes, structured output), extract pipeline
node, and conftest state fixtures needed by integration tests.

---

## Infrastructure (unchanged from P2)

| Component | Detail | Status |
|---|---|---|
| Postgres | `pgvector/pgvector:pg16`, migration `da5070439f01` applied | ✅ healthy |
| MinIO | `minio/minio:latest`, bucket `documents` | ✅ healthy |
| `make migrate` | `docker compose exec app` | ✅ works host-side |

---

## Bug fixed this phase

**`config/schemas/bank_statement.yaml` — malformed nested `items`.**
`transactions.items` was a list of single-key mappings (`- date: date`) instead of
field-schema objects (`- name: date / type: date`). Fixed to reuse the same
`name`/`type`/`required` shape recursively, so one parser handles both levels.

---

## Files — FULL (implemented this phase)

| File | What was implemented |
|---|---|
| `config/schemas/bank_statement.yaml` | Fixed `transactions.items` nested shape |
| `config/schema_loader.py` | `load_schema_model(doc_type)`: YAML → `_build_model` → `create_model` with appended `confidence: float`; mtime cache for hot-reload |
| `agents/extract_agent.py` | `extract(bytes, mime_type, doc_type)`: loads schema model, builds field-list prompt, calls `generate()` with `response_schema=`, strips `confidence` from `AgentResult.data` |
| `pipelines/nodes/extract.py` | **New.** `extract_node()`: mirrors `classify_node` — fetches bytes, calls `extract()`, returns `{extracted_fields, extract_confidence}` |
| `tests/conftest.py` | `passport_state` and `bank_statement_state` fixtures implemented (put sample PDF to minio_client, return pre-populated state dict) |
| `tests/unit/test_agents.py` | `test_extract_returns_agent_result_for_passport`, `test_extract_returns_failure_for_unknown_doc_type` unstubbed |
| `tests/integration/test_p2_p3_classify.py` | `test_doc_type_from_classify_is_used_by_extract_schema_lookup`, `test_parallel_classify_and_extract_merge_without_conflict` unstubbed |

---

## Files — still STUB

| Module | Files |
|---|---|
| `agents/` | `validate_agent.py` |
| `pipelines/` | `graph.py`, `router.py` |
| `pipelines/nodes/` | `normalize.py`, `op_a_retry.py`, `op_b_hitl.py` |
| `adapters/object_store/` | `gcs_store.py` |
| `adapters/trigger/` | `pubsub_trigger.py` |
| `io_pipeline/` | `output_writer.py` |
| `query/` | `retriever.py`, `synthesizer.py` |
| `db/` | `checkpointer.py`, `vector_store.py` |
| `api/routes/` | `query.py` |
| `frontend/review_app.py` | |
| `observability/tracing.py` | |
| `scripts/` | `seed_db.py`, `run_local_demo.py` |

---

## Test results

`pytest -m "not live"` → **46 failed, 30 passed, 4 deselected in ~4s**

P2 baseline was 50 failed / 26 passed. Gain: +4 passing, -4 stub failures. Zero regressions.

### P3 target tests — all passing ✅

| Suite | Tests |
|---|---|
| `test_agents.py` | `test_extract_returns_agent_result_for_passport`, `test_extract_returns_failure_for_unknown_doc_type` |
| `test_p2_p3_classify.py` | `test_doc_type_from_classify_is_used_by_extract_schema_lookup`, `test_parallel_classify_and_extract_merge_without_conflict` |

---

## Key decisions

| Decision | Rationale |
|---|---|
| `confidence` appended to extracted schema model | Single Gemini call returns fields + confidence in one JSON blob; avoids second API call or post-hoc scoring. `extract()` strips it from `AgentResult.data` before returning |
| `Literal[tuple(field["enum"])]` is correct | Python's `x[a, b, c]` and `x[(a, b, c)]` both pass the same tuple to `__getitem__`; `Literal` receives and flattens it identically — no hand-rolling needed |
| Optional fields typed as `Optional[py_type]` with `None` default | `(py_type, None)` in Pydantic v2 `create_model` would accept `None` but type as `py_type`; using `Optional[py_type]` makes the intent explicit and avoids validation errors |
| `getattr(parsed, "confidence", 0.0)` instead of `parsed.confidence` | `model` is typed as `type[BaseModel]`; mypy can't see the dynamically-added field. `getattr` avoids `# type: ignore` while remaining safe |
| `extract(bytes, mime_type, doc_type)` — same signature as classify | Option B (multimodal bytes) is consistent; `doc_type` third because it's the lookup key, not a document property |
| `extract_node` reads `state.get("doc_type") or ""` | Guards against `None` doc_type (unclassified document); `extract()` returns `success=False` with `reason` for missing schema, which validate/retry can handle |
| mtime cache in `load_schema_model` | Schema files change rarely; cache avoids re-parsing on every call while still picking up edits during local development without restarting the app |

---

## What P3 does NOT do

- No validate agent (`validate_agent.py`) — P4 scope
- No pipeline graph wiring — P5 scope
- No normalize node / router — P5/P6 scope
- No HITL logic — P6 scope
- No vector embeddings or query route
- No GCP deployment
