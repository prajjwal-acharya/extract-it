# P5-HITL — Human-in-the-Loop

**Status:** ✅ Done  
**Scope:** op_b_hitl node, LangGraph interrupt/resume, review API, LearningPolicy, schema proposals

---

## What P5-HITL delivered

HITL (Human-in-the-Loop) is the escalation path for documents that the automated
pipeline cannot confidently resolve. The pipeline pauses, a human reviewer sees the
extracted fields (with the ability to correct them), approves or rejects, and the
pipeline resumes exactly where it left off.

---

## When HITL triggers

`ResolutionPlanner` selects `Strategy.HITL` when:
- `retry_count ≥ max_retries` (automated retries exhausted)
- `TruthReport.persistence.requires_human=True` (hard human-required flag)

`route_after_executor` routes to `op_b_hitl` when the strategy is `HITL`.

---

## op_b_hitl_node

`pipelines/nodes/op_b_hitl.py`:

```python
def op_b_hitl_node(state: GraphState) -> dict:
    payload = build_reviewer_payload(state)   # from pipelines/learning/reviewer_payload.py
    interrupt(payload)                         # LangGraph suspends here
    # Execution resumes here after POST /review/{id}/decision
    decision = get_last_interrupt_response()   # {"approved": bool, "corrections": dict}
    ...
    return {
        "hitl_required": True,
        "hitl_approved": decision["approved"],
        "extracted_fields": merged_fields,     # apply any corrections
        "hitl_correction": bool(corrections),
    }
```

`interrupt(payload)` checkpoints the full `GraphState` to Postgres and suspends.
The graph does not continue until `graph.invoke(Command(resume=...))` is called.

---

## LangGraph interrupt/resume

### Suspend (interrupt)

```
op_b_hitl_node calls interrupt(payload)
        │
        ▼
LangGraph checkpoints GraphState to Postgres (thread_id = document_id)
Document.current_phase = "awaiting_review"
Graph execution suspended — thread returns immediately to caller
```

### Resume (decision submitted)

```
POST /review/{id}/decision  {"approved": true, "corrections": {...}}
        │
        ▼
api/routes/review.py
  1. Validate X-API-Key header (if REVIEW_API_KEY is set)
  2. graph.invoke(
         Command(resume={"approved": approved, "corrections": corrections}),
         config={"configurable": {"thread_id": document_id}}
     )
  3. op_b_hitl_node resumes after interrupt()
  4. Merges corrections into extracted_fields
  5. Sets hitl_approved, hitl_correction in GraphState
  6. Pipeline continues: normalize → persist → END
  7. Return {"status": "resumed", "document_id": document_id}
```

The response returns immediately (does not wait for pipeline completion).
Pipeline runs in a background thread.

---

## Review API

### GET /review/pending

Returns all documents with `current_phase = "awaiting_review"`. For each document,
extracted fields are read from the **LangGraph checkpoint** (not from `doc.extracted_fields`,
which is still empty at interrupt time — `write_output` hasn't run yet):

```python
config = {"configurable": {"thread_id": doc.id}}
snapshot = graph.get_state(config)
checkpoint_fields = snapshot.values.get("extracted_fields") or {}
```

Falls back to `doc.extracted_fields` if the checkpoint is unavailable.

Response includes:
- `document_id`, `filename`, `doc_type`
- `extracted_fields` (from checkpoint)
- `confidence_logs` (classify + extract scores)
- `retrieval_context` (similar documents used as RAG examples)

### POST /review/{id}/decision

```json
{
  "approved": true,
  "corrections": {
    "surname": "SMITH",
    "passport_number": "A12345678"
  }
}
```

`corrections` is a partial dict — only fields the reviewer wants to change. The node
merges corrections over the existing `extracted_fields`.

Auth: `X-API-Key: <REVIEW_API_KEY>` header required if env var is set. Route is
open in dev mode when `REVIEW_API_KEY` is unset.

---

## HITL outcome in persist

`_compute_terminal_status` in `io_pipeline/output_writer.py`:

```python
# Priority 1: rejection is final.
if state.get("hitl_required") and not state.get("hitl_approved"):
    return "rejected"

# Priority 2: approval overrides all automated verifier failures.
if state.get("hitl_required") and state.get("hitl_approved"):
    return "completed"
```

Human approval unconditionally returns `"completed"` — the human reviewer has
accepted responsibility for the document's correctness, overriding any verifier
failures recorded in the TruthReport.

---

## route_after_hitl

```python
def route_after_hitl(state: GraphState) -> str:
    return "normalize"   # Always — regardless of approve or reject
```

Both approved and rejected documents go through `normalize` before `persist`.
This ensures `universal_schema` is always computed. `persist` reads `hitl_approved`
to decide the final status.

---

## LearningPolicy

`pipelines/learning/policy.py` — sole authority for embedding decisions. Called by
`write_output` to determine whether and how to embed after a HITL run:

```python
class LearningPolicy:
    def evaluate(
        self,
        resolution_decision: ResolutionDecision,
        truth_report: TruthReport,
        execution_history: list[ExecutionRecord],
        is_human_correction: bool,
    ) -> LearningDecision:
        ...
```

`LearningDecision` fields:
- `allow_learning: bool` — whether to embed at all
- `learn_from_document: bool` — source = `"document"` (auto embed)
- `learn_from_correction: bool` — source = `"hitl_correction"` (human-corrected embed)
- `schema_candidate: bool` — whether to create a `SchemaProposalRecord`
- `reason: str`

HITL-corrected documents with `learn_from_correction=True` are re-embedded with
`source="hitl_correction"`, creating higher-quality few-shot exemplars for future RAG.

---

## Schema proposals

When `schema_diff_agent` discovers new fields during a retry, and the run ultimately
succeeds (via HITL approval or auto-accept), `LearningPolicy` may set
`schema_candidate=True`. `write_output` then creates a `SchemaProposalRecord`:

```python
SchemaProposalRecord(
    doc_type=...,
    proposed_version="1.1",
    additions_json=[{"name": "new_field", "type": "string", ...}],
    relaxed_fields_json=[{"name": "formerly_required_field", ...}],
    origin_document_id=document_id,
    status="pending",
)
```

Schema proposals require explicit human approval via `POST /schema-proposals/{id}/approve`.
Approval activates the new `SchemaVersion`. Rejection stores the reason; the record
is never deleted (fully auditable).

---

## Reviewer payload

`pipelines/learning/reviewer_payload.py` builds the `interrupt()` payload:

```python
def build_reviewer_payload(state: GraphState) -> dict:
    return {
        "document_id": state["document_id"],
        "filename": state["filename"],
        "doc_type": state.get("doc_type"),
        "extracted_fields": state.get("extracted_fields") or {},
        "validation_issues": state.get("validation_issues") or [],
        "confidence": state.get("extract_confidence"),
        "truth_report_summary": {...},
        "resolution_reason": state.get("resolution_decision").reason if ... else None,
    }
```

This is what `GET /review/pending` exposes to the Review Queue UI.
