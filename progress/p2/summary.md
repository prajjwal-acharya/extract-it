# P2 — Classify + Master + LLM Client

**Status:** Complete ✅
**Branch:** `main`

---

## What this phase covers

LLM client wiring (google-genai SDK), document classification agent (multimodal bytes),
master pipeline node (filename-based doc_type pre-population), classify pipeline node,
and a shared utilities layer (DRY fix for filename regex + MIME map that were duplicated
across ingestion and pipeline code).

---

## Infrastructure (unchanged from P1)

| Component | Detail | Status |
|---|---|---|
| Postgres | `pgvector/pgvector:pg16`, migration `da5070439f01` applied | ✅ healthy |
| MinIO | `minio/minio:latest`, bucket `documents` | ✅ healthy |
| FastAPI app | `/health`, `/ingest/` | ✅ up |
| `make migrate` | runs via `docker compose exec app` | ✅ works host-side |

---

## Files — FULL (implemented this phase)

| File | What was implemented |
|---|---|
| `config/settings.py` | Added `GOOGLE_API_KEY: str = ""` |
| `shared/utils/filename.py` | `parse_doc_type_from_filename()` — extracted from `ingestion.py` (DRY) |
| `shared/utils/mime.py` | `CONTENT_TYPES` map + `mime_from_filename()` — extracted from `ingestion.py` (DRY) |
| `io_pipeline/ingestion.py` | Refactored to import from `shared/utils`; no behaviour change |
| `agents/llm_client.py` | `generate()` with `lru_cache` Gemini client, `image_bytes`, `response_schema` support; `response.text or ""` for mypy |
| `agents/classify_agent.py` | `classify(content: bytes, mime_type: str) -> AgentResult`; uses `response_schema=_ClassifyResponse` (no hand-parsing) |
| `pipelines/nodes/master.py` | `master_node()`: calls `parse_doc_type_from_filename`, returns `{"doc_type": ...}` or `{}` |
| `pipelines/nodes/classify.py` | **New file.** `classify_node()`: fetches bytes from object store, calls `classify()`, returns state update |

---

## Files — still STUB (unchanged from P1)

| Module | Files |
|---|---|
| `agents/` | `extract_agent.py`, `validate_agent.py` |
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

`pytest -m "not live"` → **50 failed, 26 passed, 4 deselected in ~4s**

P1 baseline was 59 failed / 17 passed. Gain: +9 passing, -9 stub failures. Zero regressions.

### P2 target tests — all passing ✅

| Suite | Tests passing |
|---|---|
| `test_agents.py` | `test_classify_returns_agent_result`, `test_classify_confidence_is_between_zero_and_one`, `test_generate_returns_string` |
| `test_pipelines.py` | `test_graph_state_is_valid_typed_dict`, `test_parallel_fields_have_annotated_reducers`, `test_validation_issues_uses_add_reducer`, `test_master_node_parses_filename_pattern`, `test_master_node_returns_empty_dict_for_unmatched_filename` |
| `test_p2_p3_classify.py` | `test_classify_output_is_valid_graph_state_update` |

---

## Key decisions

| Decision | Rationale |
|---|---|
| `classify(content: bytes, mime_type: str)` — breaking signature change | Option B (multimodal bytes); sending raw bytes preserves layout/tables/images that text extraction discards for document classification |
| `response_schema=_ClassifyResponse` in `generate()` | Gemini structured output; eliminates fragile hand-parsing of JSON from `response.text` |
| `response.text or ""` | `response.text` is typed `str \| None` in google-genai SDK; `or ""` keeps `generate()` return type `str` and lets `classify()` exception handler catch empty-response failures cleanly |
| `lru_cache(maxsize=1)` on `_client()` | Gemini `Client` is expensive to construct; one instance per process is correct for this workload |
| `test_generate_returns_string` mocked (not live) | Keeps test in `not live` suite; avoids CI needing `GOOGLE_API_KEY`. Real-API smoke coverage deferred to `@pytest.mark.live` if added later |
| `shared/utils/` extracted this phase | `_FILENAME_RE` and `_CONTENT_TYPES` were duplicated between `io_pipeline/ingestion.py` and the planned pipeline nodes; extracted to single source before the duplication grew |
| `master_node` stays cheap/sync | Does only filename parsing; raw bytes loading belongs in `classify_node`/`extract_node` per MoE-router design intent |

---

## What P2 does NOT do

- No extract agent (`extract_agent.py`) — P3 scope
- No validate agent (`validate_agent.py`) — P4 scope
- No pipeline graph wiring (`graph.py`, `router.py`) — P5 scope
- No normalize node — P6 scope
- No HITL logic — P6 scope
- No vector embeddings or query route
- No GCP deployment
