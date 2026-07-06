# P3 — Extraction

**Status:** ✅ Done  
**Scope:** extract_agent, schema loader, Pydantic model generation, self-consistency voting, RAG context injection

---

## What P3 delivered

P3 turns raw document bytes into a typed, validated dict of extracted fields. It
introduces the `extract_agent`, the DB-first schema loader, runtime Pydantic model
generation, and the self-consistency voting pass for borderline confidences.

---

## extract_node flow

```
extract_node(state)
  1. Load active schema:
        load_schema_model(doc_type) → Pydantic model class

  2. RAG context retrieval:
        similarity_search(embed(doc_type), top_k=3, doc_type=doc_type)
        context = "\n".join(f"Example: {row.chunk_text}" for row, _ in similar)

  3. Write RetrievalLog rows (one per retrieved document, skip self-references)

  4. extract_agent(raw_bytes, mime_type, doc_type, context) → AgentResult
        See "extract_agent design" below.

  5. Update GraphState:
        extracted_fields = agent_result.data
        extract_confidence = agent_result.confidence
        extraction_result = ExtractionResult(...)
```

---

## extract_agent design

`agents/extract_agent.py` — three-phase design:

### Phase 1 — Structured extraction (`_extract_once`)

```
_extract_once(content, mime_type, doc_type, context)
  ├── load_schema_model(doc_type)             ← DB-first, YAML fallback
  ├── build_prompt(schema_fields, context)    ← agents/prompt_builder.py
  ├── llm_client.generate(
  │       prompt,
  │       image_bytes=content,
  │       mime_type=mime_type,
  │       response_schema=PydanticModel
  │   )
  └── PydanticModel.model_validate_json(raw)  ← strict Pydantic validation
```

`response_schema` constrains Gemini's output to the Pydantic model structure,
so hallucinated field names are rejected at the JSON parsing level.

### Phase 2 — Self-consistency voting (borderline confidence)

Runs only when `confidence ∈ [0.60, 0.85)`:

```
extract_agent()
  ├── _extract_once() → first result (confidence C)
  ├── if 0.60 ≤ C < 0.85:
  │     run 2 more _extract_once() passes
  │     vote(3 results):
  │       per-field: mode vote (most common value wins)
  │       tie-break: value from highest-confidence sample
  └── return voted AgentResult
```

Self-consistency is implemented in `agents/self_consistency.py`. The voted result
has a higher effective confidence because it aggregates independent LLM draws.

### Phase 3 — Deterministic verifier tool loop

After extraction (for `passport` and `bank_statement`):

```
llm_client.generate_with_tools(
    prompt,
    declarations=[FunctionDeclaration(mrz_checksum), FunctionDeclaration(balance_arithmetic)],
    fn_registry={...},
    max_tool_calls=3,
)
```

The LLM invokes the Python verifier functions directly. Result stored as
`verification_passed: bool | None` in `ConfidenceLog`.

---

## Schema loader

`config/schema_loader.py` — DB-first with YAML fallback:

```
load_schema_model(doc_type) → type[BaseModel]
  ├── _load_active_row(session, doc_type)
  │     SELECT * FROM schema_versions WHERE doc_type=? AND is_active=TRUE
  │     → SchemaVersion row
  ├── if found: use fields_json from DB row
  │   else:     use _load_yaml_raw(doc_type)["fields"]
  │
  ├── _build_model(fields_json, doc_type, version)
  │     → dynamically constructs a Pydantic BaseModel class
  │     → field types: string→str, date→str, float→float, int→int, array→list
  │     → required fields: default=None (Gemini may return null for absent fields)
  │
  └── cache by version string (LRU; cache busted on version bump)
```

The cache key is the schema version string (e.g. `"passport_1.1"`). When
`schema_diff_agent` bumps the version, the next call to `load_schema_model`
misses cache and rebuilds with the new field set.

---

## Prompt builder

`agents/prompt_builder.py` constructs the extraction prompt:

```
build_extraction_prompt(doc_type, schema_fields, rag_context)
  → "Extract the following fields from this {doc_type} document:
     - field_name (required): description
     - field_name (optional): description
     ...

     Examples from previously processed documents:
     {rag_context}

     Return only the fields listed above in JSON format."
```

When `rag_context` is empty (no similar documents found yet), the examples section
is omitted. As the database grows, retrieval quality improves.

---

## RAG context at extraction time

`extract_node` calls `similarity_search()` before the LLM:

```python
similar = similarity_search(
    session,
    embed(doc_type, task_type="RETRIEVAL_QUERY"),
    top_k=3,
    doc_type=doc_type,
)
context = "\n".join(f"Example: {row.chunk_text}" for row, _ in similar)
```

Uses `task_type="RETRIEVAL_QUERY"` for asymmetric embedding (stored embeddings use
`RETRIEVAL_DOCUMENT`). This matches the Gemini embedding model's intended usage.

Every retrieval writes a `RetrievalLog` row with `stage="first_pass"` so the
knowledge graph can trace which documents influenced which extractions.

---

## Strategy side-effects applied by op_a_retry

When `strategy_executor` has set side-effects on GraphState (from a previous retry),
`op_a_retry_node` applies them before re-calling `extract_agent`:

| Side-effect | How applied |
|---|---|
| `refined_prompt` | Passed as additional context to `_extract_once` |
| `better_retrieval_queries` | Used instead of default `doc_type` query for similarity_search |
| `preprocessed_bytes` | Replaces `raw_bytes` for the extraction call |
| `preprocessed_mime_type` | Replaces `mime_type` |
| `model_override` | Passed to `llm_client.generate` to use a different Gemini model |

After consuming them, `op_a_retry_node` clears all side-effect fields to `None` so
they don't persist into the next retry pass if a different strategy is selected.

---

## ExtractionResult

`pipelines/truth_engine/models.py` — `ExtractionResult` wraps the output of a
single extraction pass for the Truth Engine to evaluate:

```python
@dataclass
class ExtractionResult:
    doc_type: str
    extracted_fields: dict
    confidence: float
    schema_version: str | None
```

Stored in `GraphState.extraction_result` and consumed by `truth_engine_node`.
