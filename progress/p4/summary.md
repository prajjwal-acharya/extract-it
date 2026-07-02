# P4 — Validate Agent + Router

**Status:** Complete ✅
**Branch:** `main`

---

## What this phase covers

Schema-based field validation (no LLM call), routing logic after validation
(`normalize` / `op_a_retry` / `op_b_hitl`), and the `MAX_RETRIES` config ceiling.
Also catches up two tests from the P3 scope gap (`test_p3_p4_extract.py`).

---

## P3 scope-gap catch-up (noted explicitly)

`tests/integration/test_p3_p4_extract.py` has 3 tests: two
(`test_extract_output_keys_match_schema_fields`, `test_extract_output_is_valid_graph_state_update`)
depend only on `extract_node` built in P3 and should have shipped then. They are
unstubbed here. Zero behaviour change — pure test catch-up.

---

## Files — FULL (implemented this phase)

| File | What was implemented |
|---|---|
| `config/settings.py` | Added `MAX_RETRIES: int = 2` |
| `agents/validate_agent.py` | `validate(doc_type, extracted_fields)`: schema-based, no LLM; confidence = `1 - issues/total_fields`. `meets_threshold(confidence)` against `settings.CONFIDENCE_THRESHOLD` |
| `pipelines/nodes/validate.py` | **New.** `validate_node()`: calls `validate()`, returns `{validation_issues, validate_confidence}` |
| `pipelines/router.py` | `route_after_validate()`: normalize / op_a_retry / op_b_hitl. `route_after_hitl()`: normalize / end |
| `tests/unit/test_agents.py` | `test_validate_returns_issues_for_invalid_fields`, `test_validate_meets_threshold_true_above_threshold` |
| `tests/unit/test_pipelines.py` | `test_router_routes_to_normalize_above_threshold`, `test_router_routes_to_retry_when_retries_remain`, `test_router_routes_to_hitl_when_retries_exhausted` |
| `tests/integration/test_p3_p4_extract.py` | All 3 (2 P3 catch-up + `test_validate_receives_extracted_fields_and_doc_type`) |
| `tests/integration/test_p4_p5_validate.py` | All 3 |

---

## Files — still STUB

| Module | Files |
|---|---|
| `pipelines/` | `graph.py` |
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

`pytest -m "not live"` → **35 failed, 41 passed, 4 deselected in ~4s**

P3 baseline was 46 failed / 30 passed. Gain: +11 passing, -11 stub failures. Zero regressions.

### P4 target tests — all passing ✅

| Suite | Tests |
|---|---|
| `test_agents.py` | `test_validate_returns_issues_for_invalid_fields`, `test_validate_meets_threshold_true_above_threshold` |
| `test_pipelines.py` | 3 router tests |
| `test_p3_p4_extract.py` | All 3 |
| `test_p4_p5_validate.py` | All 3 |

---

## Key decisions

| Decision | Rationale |
|---|---|
| `validate()` is schema-based, not LLM-based | Reuses already-built `load_schema_model()`; deterministic, cheap, no added API cost. Per P0's "demo scope drives architecture" principle — the 12-phase secondary Groq verification pass was dropped when the plan collapsed to 9 phases |
| `confidence = 1 - issues/total_fields` | Linear degradation per failing field; intuitive and directly comparable to the `CONFIDENCE_THRESHOLD` setting |
| `total = len(model.model_fields) - 1` | Excludes the injected `confidence` field from the denominator so it doesn't artificially inflate the score |
| `MAX_RETRIES: int = 2` in settings | Makes the retry ceiling configurable per environment; `route_after_validate` uses `retry_count < MAX_RETRIES` so the first two failures retry, the third routes to HITL |
| `retry_count == MAX_RETRIES` routes to HITL | Test uses `retry_count=2` with `MAX_RETRIES=2`; `< 2` is False so `op_b_hitl` is returned — boundary is correct |

---

## What P4 does NOT do

- No normalize node — P5 scope
- No op_a_retry / op_b_hitl nodes — P5/P6 scope
- No graph wiring (`build_graph()`) — P5 scope
- No vector embeddings or query route
- No GCP deployment
