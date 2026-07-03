# P7 — RAG vector store, op_a_retry, compiled graph, ingest→graph trigger

## Deliverables shipped

| File | Change |
|---|---|
| `config/settings.py` | Added `GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` |
| `.env.example` | Same additions |
| `agents/llm_client.py` | Added `embed()` with L2-normalization |
| `db/vector_store.py` | Stub → full (`upsert_embedding`, `similarity_search` with doc_type filter) |
| `db/checkpointer.py` | Bug fix: keep `_checkpointer_cm` at module scope to prevent GC closing psycopg conn |
| `io_pipeline/output_writer.py` | Embedding population on `status == "completed"` |
| `agents/extract_agent.py` | Added `context: str | None = None` param |
| `pipelines/nodes/op_a_retry.py` | Stub → full (RAG-augmented re-extract + re-validate) |
| `pipelines/router.py` | Bug fix: `route_after_hitl` "end" → "persist" |
| `pipelines/graph.py` | Stub → full; lazy `get_graph()` singleton (avoids postgres at import) |
| `api/routes/ingest.py` | Wired `BackgroundTasks` → `get_graph().invoke()` |
| `api/routes/review.py` | Updated to `get_graph()` (removed stale `graph` reference) |
| `tests/unit/test_vector_store.py` | New — 4 tests (upsert, ordering, doc_type filter) |
| `tests/unit/test_pipelines.py` | Added 4 tests; unstubbed `test_build_graph_returns_state_graph` |
| `tests/unit/test_agents.py` | Added `test_extract_accepts_optional_context_param` |

## Bugs fixed

**Bug 1 — `route_after_hitl` returned `"end"` on rejection**
A rejected HITL doc never reached `write_output`, so `Document.status` was never set to `"rejected"`. Fixed to `"persist"` so all paths write output.

**Bug 2 — `get_checkpointer()` connection closed on GC**
`cm = PostgresSaver.from_conn_string(raw_url)` was a local variable; psycopg's `Connection.__del__` closed the connection when `cm` was collected after `get_checkpointer()` returned. Fixed by storing `_checkpointer_cm` at module scope.

## Design decisions

**Embedding source**: `extracted_fields` JSON from completed docs (few-shot context for op_a_retry, not raw bytes).

**Embedding dimensions**: 768 via `output_dimensionality=768` + manual L2-normalization (Gemini embedding-001 is not normalized below its 3072 default).

**Upsert strategy**: Query-first rather than `session.merge()` — `DocumentEmbedding.id` is a UUID PK, so merge always inserts. Query-first updates in place.

**Graph singleton**: `get_graph()` lazy-initializes to avoid connecting to postgres at import time (enables safe test imports without a live DB).

**Ingest trigger**: `BackgroundTasks` — caller receives `document_id` immediately; pipeline failure is logged, not surfaced to caller.

## Test results

```
63 passed, 23 failed (all stubs), 4 deselected
```

Baseline was 54 passed. Delta: +9 tests.

## E2E live verification

```
POST /ingest/ → document_id: 507ebd8b-b361-4160-b79c-06dbf48ee700
~30s later: status=completed, doc_type=passport
universal_schema: {"holder_name": "AHMAD AL FARSI", "id_number": "Z43R34255", "expiry_date": "10/02/2020"}
confidence_logs: classify=1.0, extract=0.98, validate=1.0
document_embeddings: chunk_index=0, embedding_dims=768 ✓
```
First genuine full-pipeline run from ingest through normalize, persist, and embedding.
