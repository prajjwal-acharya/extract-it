# P2 — Classification & Routing Engine

**Status:** Complete + Production-hardened ✅
**Branch:** `main`

---

## What this phase covers

Three successive layers, each frozen before the next began:

| Sub-phase | Label | Scope |
|---|---|---|
| P2-original | LLM client + classify node | Gemini wiring, `classify_agent`, `master_node`, `classify_node` |
| P2A | Classification Foundation | `DocumentRegistry`, `gst_invoice` fix, validation gate, UNKNOWN type |
| P2B | Routing Engine | `RoutingPlan`, `RoutingEngine`, conditional graph edges, `unknown_handler` |
| P2B-hardened | Freeze | `ConfidencePolicy.evaluate()`, 3-action enum, `classify_retry` removed, full audit logging |

Phase 1 (Ingestion) is untouched. Phase 3 (Extract) begins at `extract_node` which receives `GraphState` including `RoutingPlan`.

---

## Infrastructure (unchanged from P1)

| Component | Detail | Status |
|---|---|---|
| Postgres | `pgvector/pgvector:pg16`, migrations `da5070439f01` + `ab12cd34ef56` applied | ✅ healthy |
| MinIO | `minio/minio:latest`, bucket `documents` | ✅ healthy |
| FastAPI app | `/health`, `/ingest/` | ✅ up |

---

## Files — FULL (implemented across all P2 sub-phases)

### Agents

| File | What was implemented |
|---|---|
| `agents/llm_client.py` | `generate()` with `lru_cache` Gemini client, multimodal `image_bytes`, `response_schema` |
| `agents/classify_agent.py` | `classify(content, mime_type) → AgentResult`; prompt built from registry (no hardcoded list); `gst_invoice` replaces old `gst` |
| `agents/extract_agent.py` | `_VERIFIABLE` derived from registry `verifier_profile`; schema resolved via `registry.schema_name()` |
| `agents/validate_agent.py` | Schema resolved via `registry.schema_name()` — UNKNOWN documents validated cleanly |

### Pipeline — Registry & Routing

| File | What was implemented |
|---|---|
| `pipelines/registry.py` | `RoutingAction` enum (PROCEED / UNKNOWN / FAILURE); `ConfidencePolicy` with `proceed_threshold` + `evaluate(confidence) → RoutingAction`; `RetryPolicy`; `RegistryEntry` (7 fields); `DocumentRegistry` with `get/exists/all/schema_name`; module-level `registry` singleton |
| `pipelines/routing_engine.py` | `RoutingPlan` (11-field frozen dataclass, sole contract between P2 and P3); `ClassificationContext` (audit envelope); `RoutingEngine.route()` — delegates to `policy.evaluate()`, never reads thresholds directly; `make_classification_context()`; `ROUTING_VERSION = "2.1"` |
| `pipelines/state.py` | `classification_context: ClassificationContext \| None`; `routing_version: str \| None` added to `GraphState` |

### Pipeline — Nodes

| File | What was implemented |
|---|---|
| `pipelines/nodes/master.py` | `master_node()`: fetches raw bytes, pre-populates `doc_type` from filename regex |
| `pipelines/nodes/classify.py` | `classify_node()`: calls `classify()`, runs `RoutingEngine`, emits `ClassificationContext` + `routing_version`; structured lifecycle logging (ClassificationStarted / ClassificationComplete) |
| `pipelines/nodes/unknown_handler.py` | Terminal node for UNKNOWN / FAILURE: logs routing failure with full plan fields, sets `error` so `write_output` marks document as `failed` |

### Pipeline — Graph

| File | What changed |
|---|---|
| `pipelines/graph.py` | Conditional edge `classify →[PROCEED]→ extract`, `classify →[UNKNOWN\|FAILURE]→ unknown_handler`; `unknown_handler → persist`; `_PHASE_MAP` entries for `unknown_handler: "routing_failed"`; `classify_retry` node removed |

### Schemas

| File | What was implemented |
|---|---|
| `config/schemas/unknown.yaml` | Placeholder schema for UNKNOWN doc type — `raw_text` field, empty universal_mapping |

### Tests

| File | What was implemented |
|---|---|
| `tests/unit/test_registry.py` | 20 tests: lookup, exists, all(), UNKNOWN entry, verifier profiles, duplicate key guard, schema completeness, classify_node integration |
| `tests/unit/test_routing_engine.py` | 31 tests: RoutingPlan construction, ClassificationContext, ConfidencePolicy.evaluate(), all 3 routing actions, medium-confidence proceeds, no downstream registry lookups, graph router functions, unknown_handler |

---

## Architecture — Final Topology

```
master → classify ──[_route_after_classify]──► extract      (PROCEED)
                                             ► unknown_handler (UNKNOWN | FAILURE)

unknown_handler → persist → END

extract → validate ──[route_after_validate]──► normalize
                   │                         ► op_a_retry → validate
                   │                         ► op_b_hitl ──[route_after_hitl]──► normalize | persist
normalize → persist → END
```

**Contract between P2 and P3:** `GraphState.classification_context.routing_plan` (a `RoutingPlan`). Extract node reads `doc_type`, `schema_name`, `verifier_profile` from state — no registry re-lookup.

---

## RoutingEngine — decision precedence

```
1. result.success == False          → FAILURE   (Gemini call failed)
2. raw_doc_type not in registry     → UNKNOWN   (unrecognised type string)
3. raw_doc_type == "UNKNOWN"        → UNKNOWN   (classifier explicit)
4. policy.evaluate(confidence):
     >= proceed_threshold (0.70)    → PROCEED
     <  proceed_threshold           → UNKNOWN   (best guess in reason, not document_type)
```

The engine orchestrates. The policy decides. No threshold comparisons exist outside `ConfidencePolicy.evaluate()`.

---

## RoutingPlan fields

| Field | Description |
|---|---|
| `action` | `RoutingAction` enum: PROCEED / UNKNOWN / FAILURE |
| `document_type` | Canonical type ("UNKNOWN" for failures/low-confidence) |
| `schema_name` | Maps to `config/schemas/<name>.yaml` for extract + validate |
| `extraction_prompt_key` | Hook for future prompt versioning (Phase 2C) |
| `verifier_profile` | Tuple of verifier names (e.g. `("mrz_checksum",)`) |
| `retry_policy` | `RetryPolicy(max_retries=2)` — consumed by Phase 4 adaptive retry |
| `confidence_policy` | Per-doc policy; carried so Phase 4 can re-evaluate without registry |
| `rag_namespace` | Vector retrieval namespace per doc type |
| `confidence` | Raw confidence from classifier |
| `reason` | Human-readable routing justification (logged, queryable) |
| `routing_version` | `"2.1"` — incremented when policy semantics change |

---

## ConfidencePolicy

```python
@dataclass(frozen=True)
class ConfidencePolicy:
    proceed_threshold: float = 0.70   # replaces dual normalize/retry thresholds

    def evaluate(self, confidence: float) -> RoutingAction:
        return RoutingAction.PROCEED if confidence >= self.proceed_threshold else RoutingAction.UNKNOWN
```

Medium confidence (≥ 0.70) now **PROCEEDs** — downstream validation (Phase 4) handles adaptive retry with full document context, which is strictly more capable than re-running the same classifier call. `RECLASSIFY` and `RETRY` actions are removed; a future retry must use a different model, prompt, or context.

---

## DocumentRegistry

7 entries (6 real + UNKNOWN), each fully typed:

| doc_type | schema_name | verifier_profile | proceed_threshold |
|---|---|---|---|
| `passport` | `passport` | `(mrz_checksum,)` | 0.70 |
| `bank_statement` | `bank_statement` | `(balance_arithmetic,)` | 0.70 |
| `salary_slip` | `salary_slip` | `()` | 0.70 |
| `itr` | `itr` | `()` | 0.70 |
| `gst_invoice` | `gst_invoice` | `()` | 0.70 |
| `property_deed` | `property_deed` | `()` | 0.70 |
| `UNKNOWN` | `unknown` | `()` | 0.00 (always UNKNOWN) |

Centralized lists derived from registry:
- Classifier prompt enum (`classify_agent.py`) — built from `registry.all()`, excludes UNKNOWN
- `_VERIFIABLE` set (`extract_agent.py`) — built from entries with non-empty `verifier_profile`
- Schema lookup (`extract_agent.py`, `validate_agent.py`) — via `registry.schema_name(doc_type)`

---

## Test results

`pytest tests/unit/` → **178 passed, 14 skipped** (skipped = `@pytest.mark.live` API tests)

| Suite | Tests |
|---|---|
| `test_registry.py` | 20 |
| `test_routing_engine.py` | 31 |
| `test_pipelines.py` | 17 |
| `test_agents.py` | (included in 178 total) |
| All pre-existing P1 unit tests | pass — zero regressions |

Ruff and mypy clean.

---

## Key decisions

| Decision | Rationale |
|---|---|
| `RoutingAction` in `registry.py` | It's a policy concept; `ConfidencePolicy.evaluate()` returns it without creating a circular import with `routing_engine.py` |
| Single `proceed_threshold` replaces `normalize_threshold + retry_threshold` | RECLASSIFY duplicated the same Gemini call with no new information; medium-confidence documents enter extraction and get full deterministic validation instead |
| `RoutingPlan` is the sole P2→P3 contract | No downstream node re-queries the registry; everything required for execution is copied from the entry at routing time |
| `UNKNOWN` is first-class, not special-cased | `config/schemas/unknown.yaml`, `registry.get("UNKNOWN")`, `ConfidencePolicy(proceed_threshold=0.0)` — handled identically to any other type, terminates at `unknown_handler` |
| `classify_retry` node removed | Re-running the same model/prompt/document produces negligible new information; introduces latency and cost; the spec requires a different strategy (model/prompt/context) for any future retry |
| `FAILURE` vs `UNKNOWN` actions | FAILURE = Gemini call failed entirely (network/timeout); UNKNOWN = call succeeded but type unrecognised or confidence insufficient; both route to `unknown_handler` but reason string is distinct for alerting/metrics |
| `routing_version = "2.1"` | Allows future routing policy changes to be traced in logs and DB without code archaeology |
| Classifier prompt from `registry.all()` | Adding a new doc type to the registry automatically updates the LLM prompt — no separate list to maintain |

---

## Remaining known risks

| Risk | Severity | Notes |
|---|---|---|
| `routing_version` not persisted to DB | Low | Present in state + structured logs; DB persistence requires a Document column or ConfidenceLog entry in `write_output` — Phase 2C scope |
| Per-type `proceed_threshold` uniform (0.70) | Low | Per-entry tuning is now possible (each RegistryEntry owns its policy); calibration deferred to Phase 2C |
| `FAILURE` and `UNKNOWN` share `unknown_handler` | Low | If different remediation is needed (alerts vs manual review), split into two nodes in Phase 2C |
| `extraction_prompt_key`, `rag_namespace` carried but unused | Intentional | Hooks for Phase 2C prompt versioning and namespace-scoped retrieval; not dead code |
| `master_node` does not validate filename-derived `doc_type` against registry | Low | `classify_node` corrects it via RoutingEngine; low real-world risk since filenames are controlled |

---

## What P2 does NOT do

- No confidence calibration or active learning
- No prompt versioning or per-doc-type prompts
- No multiple Gemini models or ensemble classification
- No metadata extraction or multi-document detection
- No adaptive retry (Phase 4 scope — deterministic validation already triggers this)
- No GCP deployment
