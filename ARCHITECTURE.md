# Architecture

Adaptive Document Intelligence Platform — end-to-end reference for how
data moves through the system, what each component does, and why it was
designed that way.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Repository Layout](#2-repository-layout)
3. [Ingestion Layer](#3-ingestion-layer)
4. [LangGraph Pipeline](#4-langgraph-pipeline)
5. [Agent Stack](#5-agent-stack)
6. [Schema System](#6-schema-system)
7. [RAG and the Vector Store](#7-rag-and-the-vector-store)
8. [Knowledge Graph and Retrieval Logging](#8-knowledge-graph-and-retrieval-logging)
9. [REST API](#9-rest-api)
10. [Streamlit Dashboard](#10-streamlit-dashboard)
11. [Data Model](#11-data-model)
12. [Configuration and Adapters](#12-configuration-and-adapters)
13. [Observability](#13-observability)
14. [CI Pipeline](#14-ci-pipeline)
15. [Design Decisions](#15-design-decisions)

---

## 1. System Overview

```
  HTTP upload / file watch / Pub/Sub
              │
              ▼
  ┌───────────────────────────────────────────────────────┐
  │  io_pipeline  (ingestion)                             │
  │  raw bytes → MinIO/GCS   Document row → Postgres      │
  │  triggers pipeline as FastAPI BackgroundTask          │
  └──────────────────────┬────────────────────────────────┘
                         │
                         ▼
  ┌───────────────────────────────────────────────────────┐
  │  LangGraph pipeline  (pipelines/)                     │
  │                                                       │
  │  master → classify → extract → validate               │
  │                                    │                  │
  │                          ┌─────────┴──────────┐       │
  │                     confidence OK?         retry < MAX?│
  │                          │ yes                 │ yes   │
  │                          ▼                     ▼       │
  │                      normalize           op_a_retry    │
  │                          │             (schema diff    │
  │                          │              + RAG retry)   │
  │                          │                   │         │
  │                          │            ────► validate   │
  │                          │           retries exhausted │
  │                          │                   │ no      │
  │                          │                   ▼         │
  │                          │            op_b_hitl        │
  │                          │           (interrupt +      │
  │                          │            human review)    │
  │                          │                   │         │
  │                          └──────────┬────────┘         │
  │                                     ▼                   │
  │                              persist (write_output)     │
  │                                                         │
  │  Every node wrapped with _stamp_phase → current_phase  │
  │  Checkpointed in Postgres (langgraph-checkpoint-postgres)│
  └──────────────────────┬────────────────────────────────┘
                         │ writes
                         ▼
        ┌──────────────────────────────────────────────┐
        │  PostgreSQL + pgvector                       │
        │  documents          ← status, current_phase  │
        │  confidence_logs    ← per-agent scores       │
        │  document_embeddings ← 768-dim pgvector      │
        │  retrieval_logs     ← RAG usage edges        │
        │  schema_versions    ← versioned schemas      │
        │  LangGraph checkpoint tables                 │
        └──────────┬──────────────┬────────────────────┘
                   │              │
          ┌────────┘              └────────┐
          ▼                               ▼
  ┌────────────────┐             ┌──────────────────┐
  │  MinIO / GCS   │             │  LangSmith       │
  │  object store  │             │  (traces)        │
  └────────────────┘             └──────────────────┘
                         │
                         ▼
  ┌───────────────────────────────────────────────────────┐
  │  FastAPI  (api/)                                      │
  │  POST /ingest/                                        │
  │  GET  /documents/          GET  /documents/{id}       │
  │  GET  /documents/{id}/references                      │
  │  GET  /documents/{id}/similar                         │
  │  GET  /documents/{id}/timeline                        │
  │  GET  /documents/{id}/explain                         │
  │  POST /search/             GET  /analytics/           │
  │  GET  /knowledge-graph/                               │
  │  GET  /review/pending      POST /review/{id}/decision │
  │  GET  /schema-proposals/pending                       │
  │  POST /schema-proposals/{id}/approve|reject           │
  │  POST /query/                                         │
  └──────────────────────┬────────────────────────────────┘
                         │
                         ▼
  ┌───────────────────────────────────────────────────────┐
  │  Streamlit UI  (frontend/review_app.py)               │
  │  Ingest │ Documents │ Knowledge Map │ HITL Queue │ Query│
  └───────────────────────────────────────────────────────┘
```

---

## 2. Repository Layout

```
extract-it/
│
├── api/                      FastAPI application
│   ├── main.py               App factory, router registration
│   ├── deps.py               Shared FastAPI dependencies
│   └── routes/
│       ├── ingest.py         POST /ingest/
│       ├── documents.py      GET  /documents/*
│       ├── knowledge_graph.py GET /knowledge-graph/
│       ├── review.py         GET/POST /review/*
│       └── query.py          POST /query/
│
├── agents/                   LLM-backed agent functions
│   ├── base.py               AgentResult dataclass
│   ├── classify_agent.py     doc_type + confidence
│   ├── extract_agent.py      structured field extraction + verifier tool loop
│   ├── self_consistency.py   3-sample vote (confidence 0.60–0.85)
│   ├── schema_diff_agent.py  free-form discovery → version bump
│   ├── validate_agent.py     rule-based field validation
│   ├── verifiers.py          deterministic: MRZ check digit, balance arithmetic
│   └── llm_client.py         Gemini wrapper: generate / embed / generate_with_tools
│
├── pipelines/
│   ├── graph.py              build_graph(), _stamp_phase wrapper, lazy singleton
│   ├── state.py              GraphState TypedDict with Annotated reducers
│   ├── router.py             route_after_validate / route_after_hitl
│   └── nodes/
│       ├── master.py         fetch raw bytes, seed doc_type from filename
│       ├── classify.py       classify_agent → doc_type
│       ├── extract.py        extract_agent + RAG context + RetrievalLog writes
│       ├── validate.py       validate_agent
│       ├── normalize.py      canonical date formatting, universal_schema map
│       ├── op_a_retry.py     schema_diff_agent + RAG re-extraction + RetrievalLog
│       └── op_b_hitl.py      LangGraph interrupt() — pause for human decision
│
├── db/
│   ├── models.py             SQLAlchemy ORM: Document, ConfidenceLog,
│   │                         DocumentEmbedding, SchemaVersion, RetrievalLog
│   ├── session.py            Engine + SessionLocal factory
│   ├── vector_store.py       upsert_embedding, similarity_search → (row, distance) tuples
│   └── checkpointer.py       PostgresSaver factory for LangGraph
│
├── io_pipeline/
│   ├── ingestion.py          ingest_file(): object store put + Document row
│   └── output_writer.py      write_output(): persist results, embed, update doc_type
│
├── config/
│   ├── settings.py           Pydantic Settings (env-file aware)
│   ├── schema_loader.py      load_schema_model() — DB-first, YAML fallback
│   └── schemas/              Static YAML bootstrap files (never mutated at runtime)
│       ├── passport.yaml
│       ├── bank_statement.yaml
│       ├── gst_invoice.yaml
│       ├── salary_slip.yaml
│       ├── itr.yaml
│       └── property_deed.yaml
│
├── adapters/                 Environment-swappable implementations
│   ├── factory.py            get_object_store() / get_trigger() — ENV=LOCAL|GCP
│   ├── object_store/         MinioStore (local) | GCSStore (GCP)
│   └── trigger/              LocalWatchTrigger (inotify) | PubSubTrigger (GCP)
│
├── query/
│   ├── retriever.py          similarity_search wrapper → list[dict]
│   └── synthesizer.py        RAG answer synthesis via Gemini
│
├── frontend/
│   └── review_app.py         Streamlit 5-panel UI
│
├── observability/
│   ├── langsmith_setup.py    LangSmith tracing init
│   └── tracing.py            Custom span helpers
│
├── infra/
│   ├── docker/               Dockerfiles, init.sql (pgvector extension)
│   └── migrations/           Alembic env + version files
│
├── tests/
│   ├── unit/                 Fast tests, all I/O mocked
│   ├── integration/          testcontainers postgres; LLM mocked
│   └── e2e/                  Gated (if: false in CI until GCP deploy)
│
├── scripts/                  Manual smoke helpers
├── shared/utils/             mime detection, filename parsing
├── docker-compose.yml        Local dev stack (postgres, minio, app, frontend)
├── docker-compose.gcp-sim.yml GCP-local simulation
├── Makefile                  up / down / migrate / test / lint shortcuts
└── pyproject.toml            Dependencies, ruff, mypy, pytest config
```

---

## 3. Ingestion Layer

**Entry point:** `POST /ingest/` (FastAPI) or direct call to `ingest_file()`.

```
HTTP multipart upload
        │
        ▼
api/routes/ingest.py
  1. Sanitise filename (os.path.basename — prevent CWE-22 path traversal)
  2. Enforce 25 MB size limit (HTTP 413 on breach)
  3. Write to temp file with correct extension (mime detection from extension)
        │
        ▼
io_pipeline/ingestion.py  ingest_file()
  1. Read raw bytes
  2. object_store.put("raw/<filename>", bytes)   → MinIO or GCS
  3. parse_doc_type_from_filename(filename)        → regex: <type>_<id>_<YYYYMMDD>.<ext>
  4. INSERT INTO documents (filename, doc_type, object_key, status="queued")
  5. Return document_id
        │
        ▼
api/routes/ingest.py  (continued)
  6. background_tasks.add_task(_run_pipeline, document_id, filename, object_key)
  7. Return {"document_id": "<uuid>"} immediately — pipeline runs asynchronously
```

The filename regex extracts `doc_type` at ingestion time only when filenames follow the
`<doc_type>_<entity>_<YYYYMMDD>.<ext>` convention. Non-matching filenames store
`doc_type=NULL`; the classify agent corrects this and `write_output` persists the
classified value back to the DB.

**Object store abstraction:** `adapters/factory.py` returns `MinioStore` when
`ENV=LOCAL` and `GCSStore` when `ENV=GCP`. Both implement the same `ObjectStore`
base class (`put`, `get`, `delete`).

---

## 4. LangGraph Pipeline

### State

`pipelines/state.py` defines `GraphState` as a `TypedDict`. Fields that multiple
nodes write use `Annotated` reducers:

| Field | Reducer | Reason |
|---|---|---|
| `extracted_fields` | `_keep_last` | `op_a_retry` overwrites on each retry pass |
| `validation_issues` | `operator.add` | Accumulated across all retry passes |
| `tool_call_count` | `operator.add` | Budget tracked across the verifier loop |

All other fields are plain types (last-write wins by default).

### Graph Topology

```
master
  │
  ▼
classify
  │
  ▼
extract ──────────────────────────────────────────────────┐
  │                                                        │
  ▼                                                        │
validate                                                   │
  │                                                        │
  ├── confidence ≥ 0.85 ──► normalize ──► persist ──► END │
  │                                                        │
  ├── retry_count < MAX_RETRIES ──► op_a_retry ───────────┘
  │                                     │
  │                               (re-validates)
  │
  └── retries exhausted ──► op_b_hitl
                                │
                         ┌──────┴──────┐
                    approved?       rejected?
                         │               │
                         ▼               ▼
                     normalize        persist
                         │               │
                         ▼               ▼
                      persist           END
                         │
                         ▼
                        END
```

### Phase Stamping

Every node is wrapped with `_stamp_phase()` in `pipelines/graph.py`. The wrapper
stamps `Document.current_phase` **before** calling the node function so that
`write_output()`'s terminal assignment (`current_phase = status`) takes effect last
on the persist node:

```python
_PHASE_MAP = {
    "master": "ingested",   "classify": "classifying",
    "extract": "extracting","validate": "validating",
    "op_a_retry": "retrying","op_b_hitl": "awaiting_review",
    "normalize": "normalizing", "persist": "finalizing",
}
```

Terminal phases (`completed` / `failed` / `rejected`) are written by
`io_pipeline/output_writer.py` and override `finalizing`.

### Checkpointing

`db/checkpointer.py` provides a `PostgresSaver` instance. LangGraph serialises the
full `GraphState` to Postgres after every node, enabling:

- **HITL interrupt/resume**: `op_b_hitl_node` calls `interrupt()` — the graph
  suspends and stores state. `POST /review/{id}/decision` resumes it via
  `graph.invoke(Command(resume=decision))`.
- **Crash recovery**: a failed pipeline can be re-invoked with the same
  `thread_id` and continues from the last checkpoint.

---

## 5. Agent Stack

All agents return `AgentResult(success, confidence, data, reason, tool_calls_made, verification_passed)`.

### classify_agent

Sends the raw document bytes to Gemini with a classification prompt. Returns
`doc_type` and a confidence score.

### extract_agent

Two-phase design:

**Phase 1 — extraction**
```
_extract_once(content, mime_type, doc_type, context)
  │
  ├── load_schema_model(doc_type)       ← DB-first, YAML fallback
  ├── build prompt from schema fields
  ├── generate(prompt, image=content, response_schema=model)
  └── model.model_validate_json(raw)    ← Pydantic strict validation
```

**Phase 2 — self-consistency** (only when confidence ∈ [0.60, 0.85))
```
extract()
  ├── _extract_once() → first result
  ├── if should_vote(confidence):
  │     run 2 more _extract_once() passes
  │     vote(3 results): per-field mode vote
  │                       tie-break → highest-confidence sample
  └── return voted AgentResult
```

**Phase 3 — deterministic verification** (passport and bank_statement)

After extraction, a `generate_with_tools()` call with `FunctionDeclaration`s for
`mrz_checksum` and `balance_arithmetic` lets the LLM invoke Python verifiers:

- `mrz_checksum`: ICAO 9303 check-digit algorithm
- `balance_arithmetic`: opening + Σ(transactions) ≈ closing ± 0.01

Result is stored as `verification_passed: bool | None` in `ConfidenceLog`.

### validate_agent

Rule-based validation against the active schema. Returns issues list and
confidence score. The router uses `meets_threshold(validate_confidence)` to
decide whether to auto-accept, retry, or escalate.

### schema_diff_agent

Runs inside `op_a_retry` before re-extraction:

```
discover_fields(raw_bytes, mime_type)
  │  Loose Gemini extraction (no response_schema) — finds every visible label
  ▼
diff_schema(discovered, active_schema.fields_json)
  │  Fuzzy match (SequenceMatcher ≥ 0.82) between discovered keys and schema keys
  │  additions  = discovered keys with no close match in schema
  │  relaxed    = required scalar fields in schema absent from discovered
  ▼
if diff non-empty:
  apply_diff(session, active_row, diff, origin_document_id)
    ├── increment version (e.g. 1.0 → 1.1)
    ├── INSERT new SchemaVersion row (is_active=True)
    └── UPDATE old row (is_active=False)
        ← partial unique index enforces exactly one active row per doc_type
```

Array-type fields (`transactions`, etc.) are excluded from diff — nested
item-level schema evolution is explicitly deferred.

### llm_client

Thin wrapper over `google.genai`:

| Function | Usage |
|---|---|
| `generate(prompt, image_bytes, mime_type, response_schema)` | Structured/unstructured generation |
| `embed(text, task_type)` | 768-dim text embedding via `gemini-embedding-001` |
| `generate_with_tools(prompt, declarations, fn_registry, max_tool_calls)` | Tool-call loop (verifiers) |

`GEMINI_MODEL` defaults to `gemini-3.1-flash-lite` (250 RPD / 15 RPM free tier).
Override via `GEMINI_MODEL` env var.

---

## 6. Schema System

### Bootstrap (static YAML)

`config/schemas/<doc_type>.yaml` files define the reference schema. They are
**never mutated at runtime**. Alembic migration `d19b4c6e2f57` seeds all YAML
files into `schema_versions` on first deploy.

```yaml
# Example: config/schemas/passport.yaml
doc_type: passport
version: "1.0"
fields:
  - { name: surname,          type: string,  required: true }
  - { name: passport_number,  type: string,  required: true }
  - { name: mrz_line1,        type: string,  required: false }
  ...
universal_mapping:
  holder_name: "{given_names} {surname}"
  id_number:   passport_number
  expiry_date: date_of_expiry
```

### Runtime source of truth (`schema_versions` table)

```
doc_type | version | fields_json | is_active
─────────────────────────────────────────────
passport  | 1.0    | [...]       | FALSE   ← superseded
passport  | 1.1    | [...]       | TRUE    ← active
```

A partial unique index (`one_active_per_doctype`) enforces at most one
`is_active=TRUE` row per `doc_type`.

`load_schema_model(doc_type)` queries the active row first; falls back to YAML
only if no DB row exists. Cache key is the version string — bumped versions
automatically bust the cache.

### Universal Schema

Every completed document is mapped to three canonical fields regardless of
`doc_type`:

| Universal field | Passport source | Bank statement source |
|---|---|---|
| `holder_name` | `{given_names} {surname}` | `account_holder` |
| `id_number` | `passport_number` | `account_number` |
| `expiry_date` | `date_of_expiry` | — |

These are stored in `Document.universal_schema` (JSON) alongside the full
`Document.extracted_fields` (all doc-type-specific fields).

---

## 7. RAG and the Vector Store

### Embeddings

After a document completes successfully, `write_output()` embeds the full
`extracted_fields` JSON string using `gemini-embedding-001` (768 dimensions)
and upserts into `document_embeddings`.

HITL-corrected documents also get re-embedded: after `POST /review/{id}/decision`
resumes the pipeline, `write_output` evaluates `LearningPolicy` and — if
`learn_from_correction=True` — embeds the merged fields with `source="hitl_correction"`,
creating higher-quality exemplars for future retrieval. `review.py` does not embed
directly; `LearningPolicy` in `write_output` is the sole embedding authority.

### Retrieval at extraction time

`extract_node` and `op_a_retry_node` both call `similarity_search()` before
invoking the LLM:

```
similarity_search(session, embed(doc_type, task_type="RETRIEVAL_QUERY"), top_k=3, doc_type=doc_type)
  │  pgvector cosine distance, filtered by doc_type, ordered by distance
  └► list[tuple[DocumentEmbedding, float]]   (embedding row, cosine distance)

context = "\n".join(f"Example: {row.chunk_text}" for row, _ in similar)
```

The context string is prepended to the extraction prompt, giving the LLM
concrete examples of what a correctly-extracted document of this type looks like.

**Asymmetric task types:** the query embedding uses `task_type="RETRIEVAL_QUERY"`
while stored embeddings are stored without a task type (default
`RETRIEVAL_DOCUMENT`). This matches the asymmetric embedding model's intent.

---

## 8. Knowledge Graph and Retrieval Logging

Every similarity_search call in `extract_node` and `op_a_retry_node` writes one
`RetrievalLog` row per result (self-references are skipped):

```python
RetrievalLog(
    document_id=<current doc>,
    retrieved_document_id=<similar doc>,
    stage="first_pass" | "retry",
    similarity_score=1 - cosine_distance,
)
```

`GET /knowledge-graph/?limit=50` aggregates these into a node/edge payload:

- **Nodes**: most recent `limit` documents (id, filename, doc_type, status)
- **Edges**: `retrieval_logs` rows where both endpoints are in the node set
  (orphan edges — pointing to documents outside the window — are excluded)

The Streamlit Knowledge Map panel renders this as a force-directed graph using
`streamlit-agraph`, with nodes colored by `doc_type` and edge width proportional
to `similarity_score`.

---

## 9. REST API

Base URL: `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

### Core endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | — | Liveness probe |
| `POST` | `/ingest/` | — | Upload a document; pipeline runs as BackgroundTask |
| `GET` | `/documents/` | — | List documents; filter: `status`, `doc_type`; paginate: `limit`, `offset` |
| `GET` | `/documents/{id}` | — | Canonical explorer: extracted fields, truth report, resolution decision, learning decision, persistence audit, confidence logs, retrieval history |
| `GET` | `/documents/{id}/references` | — | `retrieval_logs` rows for this document joined to referenced doc metadata |
| `GET` | `/knowledge-graph/` | — | Node/edge payload for the most recent `limit` documents |
| `POST` | `/query/` | — | Semantic Q&A: `{question: str}` → `{answer, sources}` |

### Query & Explainability (read-only)

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/search/` | — | Semantic search: `{query, doc_type?, top_k}` → ranked results with similarity score and 300-char excerpt |
| `GET` | `/documents/{id}/similar` | — | Top-k documents nearest to this doc's embedding (pgvector cosine) |
| `GET` | `/documents/{id}/timeline` | — | Ordered pipeline events: agent, timestamp, confidence, duration_ms, retry labeling, HITL injection |
| `GET` | `/documents/{id}/explain` | — | Human-readable: verdict, verifier pass/fail, field coverage, learning action |
| `GET` | `/analytics/` | — | Aggregate metrics: document counts by status, acceptance/HITL/retry rates, strategy usage, verifier failures, avg confidence by agent |

### HITL & Schema Approval

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/review/pending` | — | Documents in `awaiting_review` with confidence logs and retrieval context |
| `POST` | `/review/{id}/decision` | `X-API-Key` | Resume pipeline: `{approved: bool, corrections: dict}` |
| `GET` | `/schema-proposals/pending` | — | Schema proposals with status `pending` |
| `POST` | `/schema-proposals/{id}/approve` | `X-API-Key` | Activate proposal → inserts new `SchemaVersion`, sets proposal status `approved` |
| `POST` | `/schema-proposals/{id}/reject` | `X-API-Key` | Store rejection reason; proposal status → `rejected` (auditable, never deleted) |

**API key guard**: routes marked `X-API-Key` check the header against `REVIEW_API_KEY` env var.
If the env var is unset the route is open (dev mode).

---

## 10. Streamlit Dashboard

`frontend/app.py` is the multipage entry point. Pages live in `frontend/pages/`.
All HTTP calls go through `frontend/api_client.py` — no page imports `requests` directly.

`API_BASE_URL` (default `http://localhost:8000`) controls which API the dashboard talks to.
In Docker it is set to `http://app:8000` via `docker-compose.yml`.

### Page inventory

| File | Title | Key API calls |
|---|---|---|
| `app.py` | Upload | `POST /ingest/` → live status polling via `GET /documents/{id}` |
| `pages/1_📋_Documents.py` | Documents | `GET /documents/`, `GET /documents/{id}`, `GET /documents/{id}/timeline`, `GET /documents/{id}/explain`, `GET /documents/{id}/similar` |
| `pages/2_🔍_Search.py` | Semantic Search | `POST /search/` |
| `pages/3_✅_Review_Queue.py` | Review Queue | `GET /review/pending`, `POST /review/{id}/decision` |
| `pages/4_🏛_Schema_Proposals.py` | Schema Proposals | `GET /schema-proposals/pending`, `POST /schema-proposals/{id}/approve|reject` |
| `pages/5_📊_Analytics.py` | Analytics | `GET /analytics/` |
| `pages/6_🗺_Knowledge_Map.py` | Knowledge Map | `GET /knowledge-graph/` → `streamlit-agraph` force-directed graph |

### Design constraints

- All business logic stays in the API — the dashboard is a pure presentation layer.
- No duplicate DB queries: every data fetch goes through `api_client.client`.
- Graceful degradation: `ApiError` is caught per-page and rendered as `st.error()`,
  never surfaced as an unhandled exception.
- Dark theme via `frontend/.streamlit/config.toml`.

### Running the dashboard

In Docker (automatic):
```
make up   # frontend service starts on port 8501
```

Locally against a running API:
```bash
make dashboard
# → API_BASE_URL=http://localhost:8000 streamlit run frontend/app.py
```

### Tests

`frontend/tests/test_smoke.py` — headless `AppTest` smoke tests for all 7 pages
(API mocked via `mock.patch("api_client.client", ...)`) plus `ApiClient` unit tests.

```bash
make test-smoke
```

---

## 11. Data Model

### `documents`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `filename` | string | original upload name |
| `doc_type` | string | set at ingest from filename regex; corrected by classify + write_output |
| `object_key` | string | `raw/<filename>` in object store |
| `status` | string | `queued` → `completed` / `failed` / `rejected` |
| `current_phase` | string | fine-grained pipeline phase (see DOCUMENT_PHASES) |
| `universal_schema` | JSON | 3 canonical fields |
| `extracted_fields` | JSON | full doc-type-specific extraction output |
| `created_at` | timestamp | |
| `updated_at` | timestamp | on-update |

**`current_phase` values** (in order):
`pending → ingested → classifying → extracting → validating → retrying →
awaiting_review → normalizing → finalizing → completed | rejected | failed | persist_failed`

`persist_failed` is a terminal state set when the atomic 4-phase persist succeeds
through Phase A (DB audit) but fails in Phase B (object store), Phase C (embedding),
or Phase D (terminal status commit). The document's data is safe in Postgres but the
pipeline did not reach a clean terminal state.

### `confidence_logs`

One row per agent per document.
`agent` ∈ `{classify, extract, validate, verify, schema_diff, persist}`.

The `persist` agent writes `score=1.0` on clean completion and `score=0.0` on
`persist_failed`, making persistence failures visible in the confidence timeline.

### `document_embeddings`

768-dim pgvector column. One row per chunk (chunk_index 0 = full extracted_fields JSON).

| `source` value | When written |
|---|---|
| `"document"` | Pipeline auto-embed via LearningPolicy (learn_from_document=True) |
| `"hitl_correction"` | HITL correction path (learn_from_correction=True) |

`LearningPolicy` (in `output_writer.py`) is the sole authority that decides whether and how
to embed. No other code path writes embeddings directly.

### `retrieval_logs`

Records every RAG retrieval event — which document used which other document as context:

| Column | Notes |
|---|---|
| `document_id` | the document being extracted |
| `retrieved_document_id` | the document used as RAG context |
| `stage` | `first_pass` (extract node) or `retry` (op_a_retry node) |
| `similarity_score` | `1 - cosine_distance` ∈ [0, 1] |

### `schema_versions`

Versioned schemas. Only one row per `doc_type` has `is_active=TRUE` (enforced by
partial unique index). `source` ∈ `{reference, auto_discovered}`.

### `persistence_audit_logs`

One row per pipeline run. Snapshot of the decisions made at the end of the pipeline:

| Column | Notes |
|---|---|
| `resolution_strategy` | `accept`, `retry`, `hitl`, etc. |
| `resolution_requires_human` | whether the run was escalated to HITL |
| `learning_candidate` / `allow_learning` | LearningPolicy output |
| `learn_from_document` / `learn_from_correction` | which embed path was taken |
| `schema_candidate` / `schema_proposal_json` | whether a schema change was proposed |
| `persist_status` | terminal status written to the document |
| `persist_reason` | error message if `persist_failed` |

### `schema_proposal_records`

Human-gated schema changes. Created by `write_output` when `learning_decision.schema_candidate=True`.

| `status` | Transition |
|---|---|
| `pending` | initial state; visible in `GET /schema-proposals/pending` |
| `approved` | `POST /schema-proposals/{id}/approve` → new `SchemaVersion` activated |
| `rejected` | `POST /schema-proposals/{id}/reject` → `rejection_reason` stored; row never deleted |

### LangGraph checkpoint tables

Created by `PostgresSaver.setup()` (via `make checkpointer`) outside of Alembic.
Stores serialised `GraphState` snapshots for interrupt/resume and crash recovery.

---

## 12. Configuration and Adapters

### Environment variables (`.env`)

| Variable | Default | Description |
|---|---|---|
| `ENV` | `LOCAL` | `LOCAL` = MinIO + file-watch; `GCP` = GCS + Pub/Sub |
| `DATABASE_URL` | postgres://... | SQLAlchemy DSN |
| `MINIO_ENDPOINT` | `localhost:9000` | |
| `MINIO_BUCKET` | `documents` | |
| `GOOGLE_API_KEY` | — | Required for live LLM calls |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Override to use a different model |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | |
| `EMBEDDING_DIMENSIONS` | `768` | Must match the embedding model's output |
| `CONFIDENCE_THRESHOLD` | `0.85` | Gate between auto-accept and retry |
| `MAX_RETRIES` | `2` | Attempts before HITL escalation |
| `REVIEW_API_KEY` | — | If set, gates `POST /review/{id}/decision` |
| `LANGCHAIN_API_KEY` | — | Optional LangSmith tracing |

### Adapter pattern

`adapters/factory.py` reads `ENV` and returns the appropriate implementation:

```
ENV=LOCAL  →  MinioStore   /  LocalWatchTrigger (inotify watch on /tmp/watch)
ENV=GCP    →  GCSStore     /  PubSubTrigger (Cloud Pub/Sub subscription)
```

Both pairs share the same `ObjectStore` and `Trigger` base-class interfaces so no
application code changes between environments.

---

## 13. Observability

**LangSmith** (optional): set `LANGCHAIN_API_KEY` + `LANGCHAIN_TRACING_V2=true`.
Every `graph.invoke()` call emits a trace showing node latency, LLM token counts,
tool calls, and the conditional edge taken. Unauthenticated trace uploads emit a
harmless `LangSmithAuthError` warning and are otherwise non-blocking.

**Confidence logs**: every pipeline run writes one `ConfidenceLog` row per agent.
Query them via `GET /documents/{id}` → `confidence_logs[]`.

**Phase tracking**: `Document.current_phase` is stamped at each node entry — a
lightweight in-DB timeline without needing a separate events table.

**LangGraph graph rendering** (local dev):
```python
from pipelines.graph import get_graph
png = get_graph().get_graph().draw_mermaid_png()
open("/tmp/graph.png", "wb").write(png)
```

---

## 14. CI Pipeline

`.github/workflows/ci.yml` — four jobs on every push to `main`:

```
push
  │
  ├── lint             ruff check, ruff format --check, mypy
  │
  ├── migrations       pgvector container; alembic upgrade→downgrade base→upgrade (round-trip)
  │
  ├── unit-tests       pytest tests/unit -m "not live"
  │                    All external I/O mocked (no DB, no LLM, no object store)
  │
  └── integration-tests  (needs: lint + migrations + unit-tests)
                       testcontainers postgres; LLM + object store mocked
                       pytest tests/integration -m "not live"

e2e-tests: if: false   Gated until GCP deployment
```

Python version in CI is **3.12** (local dev uses 3.11 — both are supported).

---

## 15. Design Decisions

**Why LangGraph?**
The pipeline has state that must survive across HTTP requests (HITL interrupt),
retry loops with shared counters, and conditional branching — a plain function chain
can't express this without custom machinery. LangGraph provides TypedDict state,
Postgres-backed checkpointing, and `interrupt()` for human-in-the-loop, out of the box.

**Why Annotated reducers on GraphState fields?**
LangGraph detects conflicting writes to the same state key and raises if there's
no reducer. `op_a_retry` overwrites `extracted_fields` on every pass — `_keep_last`
tells LangGraph to replace rather than merge. `validation_issues` accumulates across
all retry passes — `operator.add` appends rather than overwrites.

**Why DB-first schema loading with YAML as bootstrap?**
YAML files can't be atomically updated and versioned without custom tooling. Storing
schemas in Postgres gives: atomic version bumps, a partial unique index to enforce
one active row, and a clean audit trail of when and why each version was promoted.
YAML files remain as the human-readable bootstrap, seeded once by Alembic.

**Why stamp phase before the node (not after)?**
`write_output()` sets `current_phase = status` (completed/failed/rejected) as the
terminal transition. If the wrapper stamped after the node, it would overwrite this
with "finalizing". Stamping before means the pre-stamp is always overrideable by
the node's own DB writes.

**Why write `doc_type` back in `write_output()`?**
`ingest_file()` parses `doc_type` from the filename pattern — many real documents
don't follow that convention, so the initial DB row has `doc_type=NULL`. The
`classify_node` determines the true `doc_type` and puts it in pipeline state, but
no earlier node writes it to the DB. `write_output()` is the single place where
all final values are persisted; writing `doc_type` there means `similarity_search`
(which filters by `doc_type`) correctly finds prior documents of the same type for
future RAG lookups.

**Why retrieval_logs instead of a synthetic similarity matrix?**
A similarity plot between all document pairs is expensive to maintain and doesn't
reflect actual usage. `retrieval_logs` records only retrievals that actually
influenced an extraction — these are the real causal edges worth visualising.

**Why `streamlit-agraph` instead of a custom graph renderer?**
Minimal dependency surface for a local demo. The force-directed layout handles
disconnected nodes and varying edge weights without any configuration beyond color
and width.

**Why no autorefresh in the Streamlit UI?**
Adding an autorefresh dependency (e.g. `streamlit-autorefresh`) for a single
"watch the pipeline" use case adds a dependency with its own version surface. A
manual Refresh button satisfies the demo requirement with zero extra deps.
