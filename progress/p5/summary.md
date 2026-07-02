# P5 — HITL Node + Checkpointer + Review UI

**Status:** Complete ✅
**Branch:** `main`

---

## What this phase covers

op_b_hitl_node (interrupt → resume), PostgresSaver checkpointer singleton,
review API route, and the full 3-panel Streamlit UI. Graph wiring (ingest →
graph.invoke) is explicitly deferred to P7 — see gap note below.

---

## Graph-entrypoint gap (flagged for P7)

Nothing in the codebase calls `build_graph().invoke(...)`. `api/routes/ingest.py`
creates the Document row and stops. The op_b_hitl_node and review route are
correct and unit-testable in isolation, but cannot be exercised through a live
`graph.invoke() → interrupt → resume` cycle until P7 compiles a real graph with
the checkpointer wired in and adds the ingest→graph trigger.

`api/routes/review.py`'s `graph.invoke()` call carries `# type: ignore[attr-defined]`
because `pipelines/graph.py` currently holds `graph = None`. P7 replaces that.

---

## Files — FULL (implemented this phase)

| File | What was implemented |
|---|---|
| `db/checkpointer.py` | `get_checkpointer()`: process-wide `PostgresSaver` singleton via `from_conn_string().__enter__()` + `.setup()` |
| `pipelines/nodes/op_b_hitl.py` | `op_b_hitl_node()`: calls `interrupt(payload)`, merges corrections into `extracted_fields`, returns `{hitl_required, hitl_approved, extracted_fields}` |
| `api/routes/review.py` | **New.** `POST /{document_id}/decision`: resumes interrupted graph via `Command(resume=...)` |
| `api/main.py` | Mounts `review_router` at `/review` |
| `frontend/review_app.py` | Full 3-panel Streamlit app: Ingest, Query (P8 warning on error), Review (HITL form) |
| `tests/integration/test_p5_p6_hitl.py` | All 3 tests — mock `langgraph.types.interrupt`, call node directly |
| `tests/unit/test_db.py` | `test_checkpointer_returns_postgres_saver` — mocks `PostgresSaver.from_conn_string`, verifies singleton + `.setup()` call |

---

## Files — still STUB

| Module | Files |
|---|---|
| `pipelines/` | `graph.py` (`build_graph()` + `graph = None`) |
| `pipelines/nodes/` | `normalize.py`, `op_a_retry.py` |
| `db/` | `vector_store.py` |
| `adapters/object_store/` | `gcs_store.py` |
| `adapters/trigger/` | `pubsub_trigger.py` |
| `io_pipeline/` | `output_writer.py` |
| `query/` | `retriever.py`, `synthesizer.py` |
| `api/routes/` | `query.py` |
| `observability/tracing.py` | |
| `scripts/` | `seed_db.py`, `run_local_demo.py` |

---

## Test results

`pytest -m "not live"` → **31 failed, 45 passed, 4 deselected in ~4s**

P4 baseline: 35 failed / 41 passed. Gain: +4 passing, -4 stub failures. Zero regressions.

### P5 target tests — all passing ✅

| Suite | Tests |
|---|---|
| `test_p5_p6_hitl.py` | `test_hitl_approval_routes_to_normalize`, `test_hitl_rejection_ends_the_graph`, `test_hitl_corrections_are_merged_into_extracted_fields` |
| `test_db.py` | `test_checkpointer_returns_postgres_saver` |

---

## Key decisions

| Decision | Rationale |
|---|---|
| `interrupt()` mocked at `pipelines.nodes.op_b_hitl.interrupt` | No live graph/checkpointer needed to test the node's correction-merge and approval logic — the decision value is whatever the human sends, testable by injecting it directly |
| `get_checkpointer()` as singleton via module-level `_checkpointer` | One setup() call per process; avoids reconnecting on every request. Acceptable for demo scope; revisit with FastAPI lifespan hook for production |
| Query panel error handling | Two-branch `except`: `HTTPError` with status 500 → "ships in P8" (app reachable, route raises NotImplementedError); generic `RequestException` → same message (app not up). Neither leaks a stack trace to demo audience |
| `graph.invoke()` carries `# type: ignore[attr-defined]` | `pipelines/graph.py` has `graph = None` until P7 — suppresses the attr error without restructuring the route |

---

## What P5 does NOT do

- No `build_graph()` — P6/P7 scope
- No normalize node — P6 scope
- No op_a_retry node — P6 scope
- No ingest→graph trigger — P7 scope (flagged above)
- No vector embeddings or query route — P8 scope
- No GCP deployment
