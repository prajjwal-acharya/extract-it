# P12 — Query and Explainability API

**Status:** ✅ Done  
**Scope:** /search, /documents/{id}/similar, /documents/{id}/timeline, /documents/{id}/explain, /analytics endpoints

---

## What P12 delivered

P12 adds read-only query and explainability endpoints that let users and operators
understand what happened to any document: why was it accepted, what verifiers passed
or failed, how long each stage took, which documents influenced its extraction, and
what platform-wide metrics look like.

---

## /search endpoint

`POST /search/` — semantic search without synthesis.

Request:
```json
{"query": "passport expiry date SMITH", "doc_type": "passport", "top_k": 5}
```

Response (list):
```json
[
  {
    "document_id": "uuid...",
    "filename": "passport_john_20240115.pdf",
    "doc_type": "passport",
    "status": "completed",
    "similarity_score": 0.91,
    "excerpt": "{\"surname\": \"SMITH\", \"given_names\": \"JOHN\", \"passport_number\": ..."
  }
]
```

- `doc_type` filter is optional; omit to search across all types.
- `excerpt` is the first 300 characters of `chunk_text` (the embedded JSON).
- Implemented via `db.vector_store.similarity_search` with cosine distance.

---

## /documents/{id}/similar endpoint

`GET /documents/{id}/similar?top_k=5`

Finds documents most similar to a given document's own embedding:

```python
# Get this document's embedding
this_embedding = session.query(DocumentEmbedding).filter_by(document_id=id).first()

# Search for similar (excluding self)
results = similarity_search(session, this_embedding.embedding, top_k=top_k+1)
results = [r for r in results if r.document_id != id][:top_k]
```

Useful for "show me other passports like this one" or finding potential duplicates.

---

## /documents/{id}/timeline endpoint

`GET /documents/{id}/timeline`

Returns all `ConfidenceLog` rows for the document, ordered by `created_at`,
enriched with:
- `duration_ms`: time between consecutive events
- `is_retry`: True when `agent` is one of the retry-pass agents
- `is_hitl`: True when `agent` is associated with the HITL phase

```json
[
  {"agent": "classify", "score": 0.92, "reason": null, "created_at": "...", "duration_ms": null},
  {"agent": "extract",  "score": 0.78, "reason": null, "created_at": "...", "duration_ms": 3420},
  {"agent": "truth_engine", "score": 0.81, "reason": "2 verifiers passed", "created_at": "...", "duration_ms": 180},
  {"agent": "persist",  "score": 1.0,  "reason": "persist_success:completed", "created_at": "...", "duration_ms": 95}
]
```

Retry passes are identifiable by `is_retry=True`. HITL injection is shown as a gap
between the last retry event and the post-HITL normalization event.

---

## /documents/{id}/explain endpoint

`GET /documents/{id}/explain`

Human-readable explanation of the pipeline outcome:

```json
{
  "verdict": "completed",
  "confidence": 0.92,
  "decision_reason": "All required fields present; MRZ check digit passed",
  "coverage": {
    "score": 1.0,
    "required_fields_missing": [],
    "additional_fields": []
  },
  "verifiers": [
    {"name": "mrz_checksum", "passed": true, "confidence": 1.0, "details": "computed=5 expected=5"},
    {"name": "passport_date_consistency", "passed": true, "confidence": 1.0, "details": "..."}
  ],
  "learning_action": "embedded as document",
  "schema_version": "1.0",
  "hitl_required": false
}
```

Pulls from `TruthAuditLog` (verifiers), `PersistenceAuditLog` (learning_action),
and `Document` (status, schema_version).

---

## /analytics endpoint

`GET /analytics/`

Aggregate metrics across all documents:

```json
{
  "document_counts": {"completed": 42, "rejected": 5, "failed": 2, "queued": 1},
  "acceptance_rate": 0.84,
  "hitl_rate": 0.18,
  "retry_rate": 0.31,
  "avg_confidence_by_agent": {
    "classify": 0.87,
    "extract": 0.79,
    "truth_engine": 0.83,
    "persist": 0.96
  },
  "verifier_failures": {
    "balance_arithmetic": 3,
    "mrz_checksum": 1
  },
  "strategy_usage": {
    "ACCEPT": 42,
    "RETRY": 18,
    "HITL": 10,
    "REJECT": 2
  }
}
```

Computed from:
- `documents` table: counts by status
- `confidence_logs`: average scores grouped by agent
- `truth_audit_logs`: verifier failure counts (verification_reports JSON array)
- `persistence_audit_logs`: strategy usage counts

---

## /documents/{id}/references endpoint

`GET /documents/{id}/references`

Returns `RetrievalLog` rows where `document_id = id`, joined to the retrieved
document's metadata:

```json
[
  {
    "retrieved_document_id": "uuid...",
    "retrieved_filename": "passport_jane_20230501.pdf",
    "retrieved_doc_type": "passport",
    "retrieved_status": "completed",
    "stage": "first_pass",
    "similarity_score": 0.88
  }
]
```

Shows which documents were used as RAG few-shot examples when this document was
extracted. The Knowledge Map uses the aggregated version of this data.

---

## Full document detail endpoint

`GET /documents/{id}` — the canonical explorer endpoint. Returns:

```json
{
  "id": "uuid...",
  "filename": "passport_john.pdf",
  "doc_type": "passport",
  "status": "completed",
  "current_phase": "completed",
  "universal_schema": {"holder_name": "JOHN SMITH", "id_number": "A12345678", "expiry_date": "2029-06-15"},
  "extracted_fields": {...},
  "confidence_logs": [...],
  "truth_audit": {...},
  "persistence_audit": {...},
  "retrieval_history": [...]
}
```

All sub-fields are optional — the endpoint returns whatever audit data exists for
the document (early-failed documents may have only `confidence_logs`).
