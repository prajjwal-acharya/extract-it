# P6 — RAG Retry

**Status:** ✅ Done  
**Scope:** pgvector embeddings, op_a_retry, schema_diff_agent, retrieval logging, knowledge graph

---

## What P6 delivered

P6 connects the retry loop to the vector store. When the pipeline retries extraction,
it now retrieves similar previously-processed documents as few-shot examples for the
LLM. This makes each retry smarter than a plain re-extraction. P6 also wires up
the knowledge graph by recording every retrieval as a `RetrievalLog` edge.

---

## op_a_retry_node flow

```
op_a_retry_node(state)
  1. Apply strategy side-effects from GraphState:
        refined_prompt       → passed to extract_agent
        better_retrieval_queries → used for RAG query instead of doc_type
        preprocessed_bytes   → replaces raw_bytes
        model_override       → passed to llm_client

  2. Clear all side-effect fields to None in the return dict

  3. Run schema_diff_agent (before re-extraction):
        discovered = discover_fields(raw_bytes, mime_type)
        diff = diff_schema(discovered, active_schema)
        if diff non-empty:
            apply_diff(session, doc_type, diff, document_id)
            → version bumped (e.g. 1.0 → 1.1)
            → cache busted on next load_schema_model call
            → SchemaProposal stored in state for LearningPolicy

  4. RAG retrieval:
        queries = better_retrieval_queries or [doc_type]
        similar = similarity_search(embed(query, "RETRIEVAL_QUERY"), top_k=3, doc_type=doc_type)
        context = "\n".join(f"Example: {row.chunk_text}" for row, _ in similar)
        Write RetrievalLog(stage="retry") for each retrieved doc

  5. Re-run extract_agent with updated context, schema, and side-effects

  6. Increment retry_count

  7. Return updated extracted_fields, extract_confidence, retry_count
     (feeds back into truth_engine → resolution_planner → ...)
```

---

## pgvector and document_embeddings

`db/vector_store.py`:

```python
def upsert_embedding(
    session,
    document_id: str,
    chunk_index: int,
    chunk_text: str,
    embedding_vec: list[float],
    source: str = "document",
) -> None:
    ...

def similarity_search(
    session,
    query_embedding: list[float],
    top_k: int = 5,
    doc_type: str | None = None,
) -> list[tuple[DocumentEmbedding, float]]:
    # pgvector cosine distance, filtered by doc_type (via JOIN)
    # ordered by distance ASC (smallest distance = most similar)
    ...
```

`DocumentEmbedding` table:
- `embedding`: 768-dim pgvector `Vector(768)` column
- `chunk_text`: the `extracted_fields` JSON that was embedded
- `source`: `"document"` or `"hitl_correction"`
- `chunk_index`: 0 (whole-document embedding; chunking deferred to future phases)

---

## Asymmetric task types

Stored embeddings are created with default task type (`RETRIEVAL_DOCUMENT`):
```python
embed(chunk_text)   # task_type=RETRIEVAL_DOCUMENT (default)
```

Query-time embeddings use `RETRIEVAL_QUERY`:
```python
embed(doc_type, task_type="RETRIEVAL_QUERY")
```

This matches Gemini's asymmetric embedding design: queries and documents are encoded
differently so that query-document similarity is higher than document-document
similarity for the same content.

---

## Retrieval logging

Every `similarity_search` call in `extract_node` and `op_a_retry_node` writes one
`RetrievalLog` row per result:

```python
RetrievalLog(
    document_id=state["document_id"],       # the document being extracted
    retrieved_document_id=row.document_id,  # the exemplar retrieved
    stage="first_pass" | "retry",
    similarity_score=1.0 - cosine_distance,
)
```

Self-references (when the document's own embedding appears in results) are skipped.

These are **real causal edges** — they record which documents actually influenced
which extraction, not a synthetic pairwise similarity matrix.

---

## Knowledge graph from retrieval logs

`GET /knowledge-graph/?limit=50` aggregates `retrieval_logs`:

```
Nodes: most recent limit documents (id, filename, doc_type, status)
Edges: retrieval_logs rows where BOTH document_id AND retrieved_document_id
       are in the node set (orphan edges excluded)
       edge.weight = similarity_score
```

The Streamlit Knowledge Map renders this as a force-directed graph using
`streamlit-agraph`, with:
- Node color: by `doc_type`
- Edge width: proportional to `similarity_score`

---

## schema_diff_agent

`agents/schema_diff_agent.py` — runs inside `op_a_retry` before re-extraction:

### Discover

```python
discover_fields(raw_bytes, mime_type) → dict
```

Loose Gemini extraction with no `response_schema` — asks "list every field label
you can see in this document." Returns a flat dict of discovered field names.

### Diff

```python
diff_schema(discovered: dict, active_fields: list[dict]) → SchemaDiff
```

Fuzzy string matching (Python `SequenceMatcher`, threshold ≥ 0.82):
- `additions`: discovered keys with no close match in the active schema
- `relaxed_fields`: required scalar fields in schema absent from discovered keys

Array-type fields (`transactions`, `allowances`, etc.) are excluded from the diff —
nested item-level schema evolution is explicitly deferred.

### Apply

```python
apply_diff(session, active_row, diff, origin_document_id) → SchemaVersion
```

If the diff is non-empty:
1. Increment version string (e.g. `"1.0"` → `"1.1"`)
2. INSERT new `SchemaVersion` row (`is_active=True`)
3. UPDATE old row (`is_active=False`)

The partial unique index `one_active_per_doctype` enforces exactly one active row
per `doc_type` at the database level.

The extraction on the same retry pass picks up the new schema immediately because
`load_schema_model` is called after `apply_diff`.

---

## Retry count management

`GraphState.retry_count` starts at 0. `op_a_retry_node` increments it on each pass.
`ResolutionPlanner` checks `retry_count` against `RetryPolicy.max_retries` (default 2)
to determine whether more retries are available.

When `retry_count ≥ max_retries`, `ResolutionPlanner` selects `HITL` regardless of
the `TruthReport` content.
