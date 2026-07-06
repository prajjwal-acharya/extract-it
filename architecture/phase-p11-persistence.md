# P11 — Atomic Persistence

**Status:** ✅ Done  
**Scope:** Atomic 4-phase write, PersistenceAuditLog, SchemaProposalRecord, schema proposals API, persist_failed status

---

## What P11 delivered

P11 makes the pipeline's final write operation atomic and fully auditable. Every
pipeline run now produces a complete audit trail covering the resolution decision,
learning decision, schema proposal, and the outcome of each write phase. If any
write phase fails, the document transitions to `persist_failed` — a visible,
recoverable terminal state — rather than silently appearing as `completed`.

---

## write_output — 4-phase atomic write

`io_pipeline/output_writer.py` — `write_output(state)`:

### Phase A — DB audit rows

```python
doc = session.get(Document, state["document_id"])
doc.doc_type = state.get("doc_type") or doc.doc_type
doc.universal_schema = state.get("universal_schema") or {}
doc.extracted_fields = state.get("extracted_fields") or {}

_write_confidence_logs(session, state, truth_report)
if truth_report:
    _write_truth_audit(session, state, truth_report)
_write_persistence_audit(session, state, resolution_decision, learning_decision,
                          schema_proposal_dict, persist_status=terminal_status)
if schema_candidate:
    _write_schema_proposal_record(session, state, schema_proposal_dict)

session.commit()   # ← Phase A committed
```

Document status stays at `"finalizing"` (set by `_stamp_phase`) until Phase D.

### Phase B — Object store

```python
store = get_object_store()
payload = json.dumps(state.get("universal_schema") or {}).encode()
store.put(f"output/{state['document_id']}.json", payload, content_type="application/json")
```

Writes the universal schema JSON to MinIO/GCS at `output/<document_id>.json`.

### Phase C — Embedding (gated)

```python
if learning_decision is not None and learning_decision.allow_learning:
    chunk_text = json.dumps(state.get("extracted_fields") or {})
    embedding_vec = embed(chunk_text)
    source = "hitl_correction" if learning_decision.learn_from_correction else "document"
    upsert_embedding(session, state["document_id"], 0, chunk_text, embedding_vec, source=source)
```

Only runs when `LearningPolicy.allow_learning=True`. This is the only code path
that writes to `document_embeddings`.

### Phase D — Terminal status

```python
doc.status = terminal_status          # "completed" | "rejected" | "failed" | ...
doc.current_phase = terminal_status
session.add(ConfidenceLog(
    document_id=state["document_id"],
    agent="persist",
    score=1.0,
    reason=f"persist_success:{terminal_status}",
))
session.commit()   # ← Phase D committed
```

---

## Failure handling

If any phase raises an exception:

```python
except Exception as exc:
    persist_reason = str(exc)
    session.rollback()
    doc.status = "persist_failed"
    doc.current_phase = "persist_failed"
    session.add(ConfidenceLog(
        document_id=...,
        agent="persist",
        score=0.0,
        reason=f"persist_failed:{persist_reason[:500]}",
    ))
    session.commit()
    raise
```

`persist_failed` is a visible terminal state. Phase A data (audit rows, extracted_fields)
may be committed even when later phases fail. The document's data is safe in Postgres
but the pipeline did not reach a clean terminal state. Manual recovery: inspect the
confidence log for `agent="persist"` to find the failure reason, then re-run.

---

## _compute_terminal_status

Priority order:

```python
def _compute_terminal_status(state, truth_report):
    # 1. Human rejection is final.
    if state.get("hitl_required") and not state.get("hitl_approved"):
        return "rejected"

    # 2. Human approval overrides all automated verifier failures.
    if state.get("hitl_required") and state.get("hitl_approved"):
        return "completed"

    # 3. Truth report that explicitly allows completion.
    if truth_report is not None and truth_report.persistence.allow_completion:
        return truth_report.persistence.document_status

    # 4. Stale error flag.
    if state.get("error"):
        return "failed"

    # 5. Truth report that doesn't allow completion.
    if truth_report is not None:
        return truth_report.persistence.document_status  # "verification_failed"

    # 6. Fallback.
    return "failed"
```

Priority 2 ensures that a human who approved a document always gets `"completed"`,
even if verifiers failed. The human reviewer has accepted responsibility.

---

## PersistenceAuditLog

One row per pipeline run. Captures the full decision chain:

```python
PersistenceAuditLog(
    document_id=...,
    resolution_strategy=resolution_decision.strategy.value,
    resolution_reason=resolution_decision.reason,
    resolution_requires_human=resolution_decision.requires_human,
    learning_candidate=resolution_decision.learning_candidate,
    allow_learning=learning_decision.allow_learning,
    learn_from_document=learning_decision.learn_from_document,
    learn_from_correction=learning_decision.learn_from_correction,
    schema_candidate=learning_decision.schema_candidate,
    learning_reason=learning_decision.reason,
    schema_proposal_json=schema_proposal_dict if schema_candidate else None,
    persist_status=terminal_status,
    persist_reason=None,   # set to error message on persist_failed
)
```

Queryable via `GET /documents/{id}` → `persistence_audit`.

---

## TruthAuditLog

One row per pipeline run. Snapshot of the full `TruthReport`:

```python
TruthAuditLog(
    document_id=...,
    doc_type=...,
    final_confidence=truth_report.final_confidence,
    decision_reason=truth_report.decision_reason,
    coverage_score=truth_report.field_validation.coverage_score,
    required_fields_missing=[...],
    additional_fields=[...],
    verification_reports=[
        {"verifier_name": ..., "passed": ..., "confidence": ..., "details": ...}
    ],
    document_status=truth_report.persistence.document_status,
    allow_completion=...,
    allow_embedding=...,
    allow_learning=...,
    persistence_reason=...,
    verifier_version="1.0",
)
```

---

## SchemaProposalRecord

Created when `learning_decision.schema_candidate=True`:

```python
SchemaProposalRecord(
    doc_type=schema_proposal_dict.get("doc_type"),
    proposed_version=schema_proposal_dict.get("proposed_version"),
    additions_json=schema_proposal_dict.get("additions", []),
    relaxed_fields_json=schema_proposal_dict.get("relaxed_fields", []),
    origin_document_id=state.get("document_id"),
    status="pending",
)
```

Schema proposals API:
- `GET /schema-proposals/pending` — lists pending proposals
- `POST /schema-proposals/{id}/approve` — activates new SchemaVersion, sets status=`"approved"`
- `POST /schema-proposals/{id}/reject` — stores rejection_reason, status=`"rejected"` (never deleted)

---

## Confidence timeline (agent="persist")

The `persist` agent writes a `ConfidenceLog` row as the final signal in the
confidence timeline:
- `score=1.0`: pipeline completed cleanly
- `score=0.0`: pipeline entered `persist_failed`

This makes persistence failures visible in `GET /documents/{id}/timeline` without
needing a separate audit query.
