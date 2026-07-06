# extract-it — Architecture Reference

Adaptive Document Intelligence Platform. End-to-end reference for data flow,
component responsibilities, design decisions, and operational constraints.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Repository Layout](#2-repository-layout)
3. [Ingestion Layer (P1)](#3-ingestion-layer)
4. [LangGraph Pipeline — Full Topology](#4-langgraph-pipeline)
5. [Agent Stack (P2–P3–P8)](#5-agent-stack)
6. [Truth Engine (P4)](#6-truth-engine)
7. [Resolution Engine (P5)](#7-resolution-engine)
8. [Schema System (P3–P9)](#8-schema-system)
9. [RAG and Vector Store (P6)](#9-rag-and-vector-store)
10. [HITL — Human-in-the-Loop (P5)](#10-hitl)
11. [Normalization and Universal Schema (P10)](#11-normalization)
12. [Atomic Persistence (P11)](#12-atomic-persistence)
13. [Knowledge Graph and Retrieval Logging (P6)](#13-knowledge-graph)
14. [Query and Explainability API (P12)](#14-query-and-explainability)
15. [Streamlit Dashboard (P13)](#15-streamlit-dashboard)
16. [Data Model](#16-data-model)
17. [Configuration and Adapters](#17-configuration-and-adapters)
18. [Startup Recovery](#18-startup-recovery)
19. [Observability](#19-observability)
20. [CI Pipeline](#20-ci-pipeline)
21. [Design Decisions](#21-design-decisions)
22. [Phase Index](#22-phase-index)

---

## 1. System Overview

```
  HTTP upload / file watch / Pub/Sub
              │
              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  io_pipeline  (ingestion)                                  │
  │  raw bytes → MinIO/GCS    Document row → Postgres          │
  │  triggers pipeline as FastAPI BackgroundTask               │
  └───────────────────────────┬────────────────────────────────┘
                              │
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  LangGraph pipeline  (pipelines/)                          │
  │                                                            │
  │  master → classify ──[route]──► extract                    │
  │                        │                                   │
  │                        └───────► unknown_handler → persist │
  │                                                            │
  │  extract → truth_engine → resolution_planner               │
  │                               → strategy_executor          │
  │                                        │                   │
  │                  ┌─────────────────────┼───────────────┐   │
  │              ACCEPT               RETRY/…            HITL  │
  │                  │                    │                │   │
  │              normalize           op_a_retry       op_b_hitl│
  │                  │                    │                │   │
  │                  │           → truth_engine        normalize│
  │                  │           (retry loop)              │   │
  │                  └──────────────────────────────────────┘  │
  │                                     ▼                      │
  │                              persist (write_output)        │
  │                                                            │
  │  _stamp_phase() wraps every node → current_phase in DB     │
  │  Checkpointed in Postgres (langgraph-checkpoint-postgres)  │
  └───────────────────────────┬────────────────────────────────┘
                              │ writes
                              ▼
        ┌────────────────────────────────────────────────┐
        │  PostgreSQL + pgvector                         │
        │  documents            ← status, current_phase  │
        │  confidence_logs      ← per-agent scores       │
        │  truth_audit_logs     ← Truth Engine output    │
        │  persistence_audit_logs ← per-run decisions    │
        │  document_embeddings  ← 768-dim pgvector       │
        │  retrieval_logs       ← RAG usage edges        │
        │  schema_versions      ← versioned schemas      │
        │  schema_proposal_records ← pending schema Δs   │
        │  LangGraph checkpoint tables                   │
        └──────────────┬──────────────┬──────────────────┘
                       │              │
              ┌────────┘              └────────┐
              ▼                               ▼
    ┌──────────────────┐             ┌──────────────────┐
    │  MinIO / GCS     │             │  LangSmith       │
    │  object store    │             │  (traces)        │
    └──────────────────┘             └──────────────────┘
                              │
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  FastAPI  (api/)                                           │
  │  POST /ingest/                                             │
  │  GET  /documents/     GET  /documents/{id}                 │
  │  GET  /documents/{id}/references                           │
  │  GET  /documents/{id}/similar                              │
  │  GET  /documents/{id}/timeline                             │
  │  GET  /documents/{id}/explain                              │
  │  POST /search/        GET  /analytics/                     │
  │  GET  /knowledge-graph/                                    │
  │  GET  /review/pending POST /review/{id}/decision           │
  │  GET  /schema-proposals/pending                            │
  │  POST /schema-proposals/{id}/approve|reject                │
  │  POST /query/                                              │
  └───────────────────────────┬────────────────────────────────┘
                              │
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  Streamlit Dashboard  (frontend/)                          │
  │  7 pages: Upload │ Documents │ Search │ Review Queue       │
  │           Schema Proposals │ Analytics │ Knowledge Map     │
  └────────────────────────────────────────────────────────────┘
```

---

## 2. Repository Layout

```
extract-it/
│
├── api/                          FastAPI application
│   ├── main.py                   App factory, lifespan, startup recovery
│   ├── deps.py                   Shared FastAPI dependencies
│   └── routes/
│       ├── ingest.py             POST /ingest/
│       ├── documents.py          GET  /documents/*
│       ├── knowledge_graph.py    GET  /knowledge-graph/
│       ├── review.py             GET/POST /review/*
│       ├── search.py             POST /search/
│       ├── analytics.py          GET  /analytics/
│       ├── schema_proposals.py   GET/POST /schema-proposals/*
│       └── query.py              POST /query/
│
├── agents/                       LLM-backed agents
│   ├── base.py                   AgentResult dataclass
│   ├── classify_agent.py         doc_type + confidence via Gemini
│   ├── extract_agent.py          field extraction + self-consistency + verifier loop
│   ├── self_consistency.py       3-sample vote (confidence 0.60–0.85)
│   ├── schema_diff_agent.py      free-form discovery → fuzzy diff → version bump
│   ├── validate_agent.py         rule-based field validation (legacy)
│   ├── verifiers.py              deterministic: MRZ check digit, balance arithmetic
│   └── llm_client.py             Gemini: generate / embed / generate_with_tools
│
├── pipelines/
│   ├── graph.py                  build_graph(), _stamp_phase wrapper, lazy singleton
│   ├── state.py                  GraphState TypedDict with Annotated reducers
│   ├── registry.py               DocumentRegistry — all supported doc types
│   ├── router.py                 route_after_executor / route_after_hitl
│   ├── routing_engine.py         ClassificationContext + RoutingPlan
│   ├── nodes/
│   │   ├── master.py             fetch raw bytes, seed doc_type from filename
│   │   ├── classify.py           classify_agent → doc_type + ClassificationContext
│   │   ├── extract.py            extract_agent + RAG context + RetrievalLog writes
│   │   ├── truth_engine.py       deterministic verifiers → TruthReport
│   │   ├── resolution_planner.py TruthReport → ResolutionDecision (strategy)
│   │   ├── strategy_executor.py  execute strategy side-effects (prompt, retrieval, etc.)
│   │   ├── normalize.py          universal_schema mapping + date canonicalization
│   │   ├── op_a_retry.py         schema_diff + RAG re-extraction + RetrievalLog
│   │   ├── op_b_hitl.py          LangGraph interrupt() — pause for human decision
│   │   ├── unknown_handler.py    handles UNKNOWN/FAILURE classification outcomes
│   │   └── validate.py           legacy validate node (retained for compat)
│   ├── truth_engine/
│   │   ├── models.py             TruthReport, FieldValidationReport, VerificationReport
│   │   ├── confidence.py         composite confidence scoring
│   │   └── verifier_registry.py  VerifierSpec + VerifierRegistry per doc_type
│   ├── resolution/
│   │   ├── models.py             ResolutionDecision, Strategy enum, ExecutionRecord
│   │   ├── planner.py            ResolutionPlanner — TruthReport → Strategy
│   │   ├── executor.py           StrategyExecutor — applies side-effects
│   │   ├── directives.py         directive types for each strategy
│   │   ├── better_retrieval.py   BETTER_RETRIEVAL query generation
│   │   ├── image_preprocess.py   IMAGE_PREPROCESS rasterization
│   │   ├── model_escalation.py   MODEL_ESCALATION model selection
│   │   └── prompt_refinement.py  PROMPT_REFINEMENT prompt rewriting
│   └── learning/
│       ├── policy.py             LearningPolicy — embedding authority
│       ├── reviewer_payload.py   HITL interrupt payload builder
│       └── schema_proposal.py    SchemaProposal builder
│
├── db/
│   ├── models.py                 SQLAlchemy ORM: all tables
│   ├── session.py                Engine + SessionLocal factory + session_scope
│   ├── vector_store.py           upsert_embedding, similarity_search
│   └── checkpointer.py           PostgresSaver factory for LangGraph
│
├── io_pipeline/
│   ├── ingestion.py              ingest_file(): object store put + Document row
│   ├── orchestrator.py           IngestionOrchestrator — dedup + dispatch
│   ├── hashing.py                SHA-256 content hash for dedup
│   ├── validation.py             file type + size guards
│   └── output_writer.py          write_output(): 4-phase atomic persist
│
├── config/
│   ├── settings.py               Pydantic Settings (env-file aware)
│   ├── schema_loader.py          load_schema_model() — DB-first, YAML fallback
│   └── schemas/                  Static YAML bootstrap (never mutated at runtime)
│       ├── passport.yaml
│       ├── bank_statement.yaml
│       ├── driving_license.yaml
│       ├── aadhaar.yaml
│       ├── gst_invoice.yaml
│       ├── salary_slip.yaml
│       ├── itr.yaml
│       ├── property_deed.yaml
│       └── unknown.yaml
│
├── adapters/                     Environment-swappable implementations
│   ├── factory.py                get_object_store() / get_trigger() — ENV=LOCAL|GCP
│   ├── object_store/             MinioStore (local) | GCSStore (GCP)
│   └── trigger/                  LocalWatchTrigger | PubSubTrigger
│
├── query/
│   ├── retriever.py              similarity_search wrapper → list[dict]
│   └── synthesizer.py            RAG answer synthesis via Gemini
│
├── frontend/
│   ├── app.py                    Multipage entry point + upload page
│   ├── api_client.py             Typed HTTP client (all pages use this)
│   ├── pages/
│   │   ├── 0_Home.py             Dashboard home
│   │   ├── 1_Documents.py        Document browser (Overview/Timeline/Explain/Similar)
│   │   ├── 2_Search.py           Semantic search
│   │   ├── 3_Review_Queue.py     HITL approve/reject with per-field correction
│   │   ├── 4_Schema_Proposals.py Approve/reject pending schema changes
│   │   ├── 5_Analytics.py        Aggregate metrics charts
│   │   └── 6_Knowledge_Map.py    Force-directed retrieval graph
│   └── tests/test_smoke.py       Headless AppTest + ApiClient unit tests
│
├── observability/
│   ├── langsmith_setup.py        LangSmith tracing init
│   └── tracing.py                Custom span helpers
│
├── infra/
│   ├── docker/                   Dockerfiles, init.sql (pgvector extension)
│   ├── migrations/               Alembic env + version files
│   └── gcp/                      Cloud Run manifest, deploy scripts, EventArc trigger
│
├── tests/
│   ├── unit/                     Fast tests, all I/O mocked
│   ├── integration/              testcontainers postgres; LLM mocked
│   └── e2e/                      Gated (if: false in CI until GCP deploy)
│
├── scripts/                      Manual smoke helpers
├── shared/utils/                 mime detection, filename parsing
├── docker-compose.yml            Local dev stack
├── docker-compose.gcp-sim.yml    GCP local simulation
├── Makefile                      up / down / migrate / test / lint shortcuts
└── pyproject.toml                Dependencies, ruff, mypy, pytest config
```

---

## 3. Ingestion Layer

**Entry point:** `POST /ingest/` (FastAPI) or `LocalWatchTrigger` folder drop.

```
HTTP multipart upload
        │
        ▼
api/routes/ingest.py
  1. Sanitise filename (os.path.basename — CWE-22 path-traversal guard)
  2. Enforce 25 MB size limit (HTTP 413 on breach)
  3. MIME detection from file extension
        │
        ▼
io_pipeline/orchestrator.py  IngestionOrchestrator.ingest()
  1. SHA-256 hash of raw bytes
  2. Dedup check: SELECT id FROM documents WHERE hash = ?
     → duplicate found: return existing document_id (no re-pipeline)
     → new file: continue
  3. object_store.put("raw/<filename>", bytes) → MinIO or GCS
  4. parse_doc_type_from_filename(filename)  → regex: <type>_<entity>_<YYYYMMDD>.<ext>
  5. INSERT INTO documents (filename, doc_type, object_key, hash, status="queued")
  6. Return document_id
        │
        ▼
api/routes/ingest.py  (continued)
  7. background_tasks.add_task(_run_pipeline, document_id, ...)
  8. Return {"document_id": "<uuid>"} immediately — pipeline runs async
```

The filename regex sets `doc_type` at ingest time only when filenames follow the
`<doc_type>_<entity>_<YYYYMMDD>.<ext>` convention. Non-matching names leave
`doc_type=NULL`; classify_node corrects it; write_output persists the final value.

**Object store abstraction:** `adapters/factory.py` returns `MinioStore` when
`ENV=LOCAL` and `GCSStore` when `ENV=GCP`. Both implement `ObjectStore.put/get/delete`.

---

## 4. LangGraph Pipeline

### 4.1 GraphState

`pipelines/state.py` — `GraphState` TypedDict. Fields with multiple writers use
`Annotated` reducers:

| Field | Reducer | Why |
|---|---|---|
| `extracted_fields` | `_keep_last` | op_a_retry overwrites each retry pass |
| `validation_issues` | `operator.add` | Accumulated across all passes |
| `execution_history` | `operator.add` | Appended by each strategy_executor pass |
| `tool_call_count` | `operator.add` | Budget tracked across nodes |

### 4.2 Graph Topology (actual, from `pipelines/graph.py`)

```
master
  │
  ▼
classify ──[_route_after_classify]──► extract         (PROCEED: confidence ≥ 0.70)
                                    ► unknown_handler  (UNKNOWN | FAILURE)

unknown_handler → persist → END

extract → truth_engine → resolution_planner → strategy_executor
                                                      │
                   ┌──────────────────────────────────┼──────────────┐
                   │                                  │              │
               ACCEPT                       RETRY / PROMPT_     HITL
           (normalize)                    REFINEMENT /      (op_b_hitl)
                                          BETTER_RETRIEVAL /
                                          IMAGE_PREPROCESS /
                                          MODEL_ESCALATION
                                          (op_a_retry)
                                                │
                                          op_a_retry → truth_engine   ← retry loop
                                                                │
                                                        (full resolution cycle repeats)

                        REJECT
                     (persist, skip normalize)

op_b_hitl ──[route_after_hitl]──► normalize   (always — both approve and reject)
normalize → persist → END
```

**Key routing invariants:**
- `route_after_hitl` always returns `"normalize"` — universal_schema is computed
  for every document regardless of HITL outcome. `persist` reads `hitl_approved` to
  set the terminal status.
- `unknown_handler → persist` skips the entire extraction/truth/resolution chain.
- `op_a_retry → truth_engine` loops the full resolution cycle, not just extraction.

### 4.3 Phase Stamping

`_stamp_phase()` in `pipelines/graph.py` wraps every node and writes
`Document.current_phase` **before** the node body executes:

| Node | Phase |
|---|---|
| `master` | `ingested` |
| `classify` | `classifying` |
| `unknown_handler` | `routing_failed` |
| `extract` | `extracting` |
| `truth_engine` | `evaluating` |
| `resolution_planner` | `planning` |
| `strategy_executor` | `executing` |
| `op_a_retry` | `retrying` |
| `op_b_hitl` | `awaiting_review` |
| `normalize` | `normalizing` |
| `persist` | `finalizing` |

Terminal phases (`completed` / `rejected` / `failed` / `persist_failed`) are
written by `write_output` and override `finalizing`.

### 4.4 Checkpointing

`db/checkpointer.py` provides a `PostgresSaver` instance. LangGraph serialises the
full `GraphState` to Postgres after every node, enabling:

- **HITL interrupt/resume**: `op_b_hitl_node` calls `interrupt()` — graph suspends.
  `POST /review/{id}/decision` resumes via `graph.invoke(Command(resume=decision))`.
- **Crash recovery**: re-invoke with the same `thread_id` to resume from last checkpoint.
- **Startup recovery**: `api/main.py` lifespan scans for stranded documents
  (`status=queued AND current_phase IN _IN_PROGRESS_PHASES`) and re-queues them in
  daemon threads. Waits up to 30 s for the `documents` table to exist (handles race
  with `clear_data.sh` re-migration).

---

## 5. Agent Stack

All agents return `AgentResult(success, confidence, data, reason, tool_calls_made, verification_passed)`.

### 5.1 classify_agent

Sends raw document bytes to Gemini with a classification prompt. Returns `doc_type`
and a confidence score. Output feeds `ClassificationContext` and `RoutingPlan`.

### 5.2 extract_agent

Three-phase design:

**Phase 1 — structured extraction**
```
_extract_once(content, mime_type, doc_type, context)
  ├── load_schema_model(doc_type)      ← DB-first, YAML fallback
  ├── build prompt from schema fields + RAG context
  ├── generate(prompt, image=content, response_schema=PydanticModel)
  └── model.model_validate_json(raw)   ← Pydantic strict validation
```

**Phase 2 — self-consistency voting** (only when confidence ∈ [0.60, 0.85))
```
  ├── _extract_once() → first result
  ├── if should_vote(confidence):
  │     run 2 more _extract_once() passes
  │     vote(3 results): per-field mode vote; tie-break → highest-confidence sample
  └── return voted AgentResult
```

**Phase 3 — deterministic verifier tool loop** (passport, bank_statement)
```
generate_with_tools(prompt, FunctionDeclaration[mrz_checksum, balance_arithmetic], ...)
```
Result stored as `verification_passed: bool | None` in `ConfidenceLog`.

### 5.3 schema_diff_agent

Runs inside `op_a_retry` before every re-extraction:
```
discover_fields(raw_bytes, mime_type)
  │  Loose Gemini extraction (no response_schema) — finds all visible labels
  ▼
diff_schema(discovered, active_schema.fields_json)
  │  Fuzzy match (SequenceMatcher ≥ 0.82)
  │  additions  = discovered keys with no close match in schema
  │  relaxed    = required fields in schema absent from document
  ▼
if diff non-empty:
  apply_diff(session, active_row, diff, origin_document_id)
    ├── increment version (1.0 → 1.1)
    ├── INSERT new SchemaVersion (is_active=True)
    └── UPDATE old row (is_active=False)
```
Array-type fields (`transactions`, `allowances`, etc.) are excluded from diff.

### 5.4 llm_client

| Function | Usage |
|---|---|
| `generate(prompt, image_bytes, mime_type, response_schema)` | Structured/unstructured generation |
| `embed(text, task_type)` | 768-dim text embedding via `gemini-embedding-001` |
| `generate_with_tools(prompt, declarations, fn_registry, max_tool_calls)` | Tool-call loop |

`GEMINI_MODEL` defaults to `gemini-3.1-flash-lite`. Override via env var.

---

## 6. Truth Engine

`pipelines/nodes/truth_engine.py` → `pipelines/truth_engine/`

The Truth Engine is the post-extraction evidence layer. It replaces the old
`validate_agent` as the sole authority over extraction quality.

**What it produces:** `TruthReport` containing:
- `FieldValidationReport` — required-field coverage score, missing fields, extra fields
- `list[VerificationReport]` — one per deterministic verifier (passed, confidence, details)
- `final_confidence` — composite score
- `PersistenceDecision` — `allow_completion`, `allow_embedding`, `allow_learning`,
  `document_status`, `reason`

**Verifier registry** (`pipelines/truth_engine/verifier_registry.py`):

| doc_type | Verifiers |
|---|---|
| `passport` | `mrz_checksum`, `passport_date_consistency` |
| `bank_statement` | `balance_arithmetic`, `statement_period_ordering` |
| `gst_invoice` | `gstin_checksum`, `invoice_total_consistency` |
| `salary_slip` | `gross_consistency`, `pan_validation` |
| `itr` | `pan_validation`, `ay_fy_consistency` |
| `property_deed` | `deed_date_consistency` |
| `driving_license` | — |
| `aadhaar` | — |

Each `VerifierSpec` has an `extractor` function that maps raw `extracted_fields`
to verifier kwargs, returning `None` when required inputs are absent (verifier
skipped — not failed).

**Balance arithmetic extractor detail:**  
Uses the running `balance` column from the last transaction row as the computed
closing balance. This avoids double-counting "Opening Balance" marker rows that
LLMs include as the first transaction entry. Falls back to summing debit/credit
amounts only when no `balance` column is present.

---

## 7. Resolution Engine

`pipelines/nodes/resolution_planner.py` + `pipelines/nodes/strategy_executor.py`

The Resolution Engine translates a `TruthReport` into a concrete action.

### 7.1 ResolutionPlanner

`TruthReport` → `ResolutionDecision`:

| Strategy | When selected |
|---|---|
| `ACCEPT` | `allow_completion=True` in TruthReport |
| `RETRY` | Below threshold, retries remaining, no specific signal |
| `PROMPT_REFINEMENT` | Field coverage issues suggest prompt improvements |
| `BETTER_RETRIEVAL` | RAG context wasn't relevant enough |
| `IMAGE_PREPROCESS` | Image quality issue hinting (low-res detection) |
| `MODEL_ESCALATION` | Persistent failure suggesting a more capable model |
| `HITL` | Retries exhausted or `requires_human=True` in TruthReport |
| `REJECT` | Hard rejection (e.g., UNKNOWN doc type with no recovery path) |

`ResolutionDecision` carries: `strategy`, `reason`, `requires_human`, `learning_candidate`.

### 7.2 StrategyExecutor

Applies the strategy's side-effects to GraphState:

| Strategy | Side-effect stored in state |
|---|---|
| `PROMPT_REFINEMENT` | `refined_prompt: RefinedPrompt` |
| `BETTER_RETRIEVAL` | `better_retrieval_queries: list[str]` |
| `IMAGE_PREPROCESS` | `preprocessed_bytes`, `preprocessed_mime_type` |
| `MODEL_ESCALATION` | `model_override: str` |
| `ACCEPT / RETRY / HITL / REJECT` | No state side-effects |

`op_a_retry_node` reads and applies these side-effects during re-extraction, then
clears them (sets to `None`) so they don't leak across passes.

---

## 8. Schema System

### 8.1 Bootstrap (static YAML)

`config/schemas/<doc_type>.yaml` — reference definitions. **Never mutated at runtime.**
Alembic migration `d19b4c6e2f57` seeds all YAML files into `schema_versions` on first deploy.

```yaml
# Example: config/schemas/passport.yaml
doc_type: passport
version: "1.0"
fields:
  - { name: surname,         type: string, required: true }
  - { name: passport_number, type: string, required: true }
  - { name: mrz_line1,       type: string, required: false }
universal_mapping:
  holder_name: "{given_names} {surname}"
  id_number:   passport_number
  expiry_date: date_of_expiry
```

### 8.2 Runtime source of truth (`schema_versions`)

```
doc_type       | version | fields_json | is_active
───────────────────────────────────────────────────
passport       | 1.0     | [...]       | FALSE   ← superseded
passport       | 1.1     | [...]       | TRUE    ← active
bank_statement | 1.0     | [...]       | TRUE
```

A partial unique index (`one_active_per_doctype`) enforces exactly one `is_active=TRUE`
row per `doc_type`.

`load_schema_model(doc_type)` queries the active row first; falls back to YAML only
if no DB row exists. Cache key is the version string — version bumps bust the cache.

### 8.3 Universal Schema

Every completed document maps to three canonical fields:

| Universal field | Passport | Bank statement | Aadhaar |
|---|---|---|---|
| `holder_name` | `{given_names} {surname}` | `account_holder` | `full_name` |
| `id_number` | `passport_number` | `account_number` (→ `iban` fallback) | `aadhaar_number` |
| `expiry_date` | `date_of_expiry` | — | — |

`universal_mapping_fallback` in the YAML provides secondary resolution when the
primary mapping returns `None` (e.g., UK bank statements use IBAN instead of
account_number).

### 8.4 Supported Document Types

| doc_type | Key fields | Verifiers |
|---|---|---|
| `passport` | surname, given_names, nationality, DOB, sex, place_of_birth, issue/expiry dates, passport_number, mrz_line1/2 | mrz_checksum, passport_date_consistency |
| `bank_statement` | account_holder, account_number/iban, bank_name, opening/closing_balance, statement_period_start/end, currency, transactions[] | balance_arithmetic, statement_period_ordering |
| `driving_license` | full_name, license_number, DOB, issue/expiry dates, address, vehicle_classes | — |
| `aadhaar` | aadhaar_number, full_name, DOB, gender, address, vid | — |
| `gst_invoice` | gstin, invoice_number, invoice_date, seller/buyer, HSN, tax_breakdown, totals | gstin_checksum, invoice_total_consistency |
| `salary_slip` | employee_name, PAN, UAN, employer, pay_period, basic, allowances, deductions, net_pay | gross_consistency, pan_validation |
| `itr` | PAN, assessment_year, financial_year, ITR_form, gross_income, tax_paid, refund, acknowledgement | pan_validation, ay_fy_consistency |
| `property_deed` | deed_type, executant, claimant, property_description, area, consideration, execution_date, registration_date | deed_date_consistency |

---

## 9. RAG and Vector Store

### 9.1 Embeddings

After a document completes, `write_output()` embeds the full `extracted_fields` JSON
using `gemini-embedding-001` (768 dimensions) and upserts into `document_embeddings`.

HITL-corrected documents are re-embedded with `source="hitl_correction"` when
`LearningPolicy.learn_from_correction=True`.

**Asymmetric task types:** stored embeddings use the default (`RETRIEVAL_DOCUMENT`);
query-time embeddings use `task_type="RETRIEVAL_QUERY"`. This matches the model's
intended asymmetric usage.

### 9.2 Retrieval at extraction time

Both `extract_node` and `op_a_retry_node` call `similarity_search()` before the LLM:

```
similarity_search(session, embed(doc_type, task_type="RETRIEVAL_QUERY"), top_k=3, doc_type=doc_type)
  │  pgvector cosine distance, filtered by doc_type, ordered by distance
  └► list[tuple[DocumentEmbedding, float]]

context = "\n".join(f"Example: {row.chunk_text}" for row, _ in similar)
```

The context string prepends the extraction prompt — concrete few-shot examples from
previously processed documents of the same type.

---

## 10. HITL

**Trigger:** `op_b_hitl_node` calls `langgraph.interrupt()` — the graph suspends
and the full `GraphState` is checkpointed to Postgres.

**Review Queue API:** `GET /review/pending` returns pending documents. Extracted
fields are read from the LangGraph checkpoint (not from `doc.extracted_fields`,
which is still empty at interrupt time).

**Resume:** `POST /review/{id}/decision` with `{approved: bool, corrections: dict}`:
```python
graph.invoke(Command(resume={"approved": approved, "corrections": corrections}), config=config)
```

**HITL approval status in persist:**
- `hitl_approved=True` → `_compute_terminal_status` returns `"completed"` unconditionally,
  overriding any verifier failures from the TruthReport.
- `hitl_approved=False` → returns `"rejected"` unconditionally.

**Route after HITL:** always `normalize` (then `persist`). Universal schema is
always computed regardless of approval decision.

---

## 11. Normalization

`pipelines/nodes/normalize.py`

Maps `extracted_fields` to three canonical `universal_schema` keys using
`universal_mapping` from the active schema:

```python
for key in ("holder_name", "id_number", "expiry_date"):
    value = _resolve(mapping.get(key), fields)
    if value is None and key in fallback_mapping:
        value = _resolve(fallback_mapping[key], fields)
    universal[key] = value
universal["expiry_date"] = _canonicalize_date(universal["expiry_date"])
```

`_resolve` handles both plain field names (`"account_holder"`) and format strings
(`"{given_names} {surname}"`).

`_canonicalize_date` normalises various date string formats to ISO 8601 (`YYYY-MM-DD`).

---

## 12. Atomic Persistence

`io_pipeline/output_writer.py` — `write_output(state)`

Four-phase atomic write:

```
Phase A — DB audit rows
  ├── doc.doc_type = state.doc_type
  ├── doc.universal_schema = state.universal_schema
  ├── doc.extracted_fields = state.extracted_fields
  ├── ConfidenceLog rows (classify, extract, truth_engine, schema_diff)
  ├── TruthAuditLog (if truth_report present)
  ├── PersistenceAuditLog (resolution + learning decisions)
  └── SchemaProposalRecord (if schema_candidate=True)
  → session.commit()

Phase B — Object store
  └── store.put("output/{document_id}.json", universal_schema_json)

Phase C — Embedding (gated on LearningPolicy)
  └── if allow_learning: embed(extracted_fields_json) → upsert_embedding()

Phase D — Terminal status
  ├── doc.status = terminal_status
  ├── doc.current_phase = terminal_status
  └── ConfidenceLog(agent="persist", score=1.0)
  → session.commit()

Any failure → rollback → doc.status = "persist_failed"
                        + ConfidenceLog(agent="persist", score=0.0)
```

`_compute_terminal_status` priority order:
1. `hitl_required AND NOT hitl_approved` → `"rejected"`
2. `hitl_required AND hitl_approved` → `"completed"`
3. `truth_report.allow_completion=True` → `truth_report.document_status`
4. `state.error` → `"failed"`
5. `truth_report` present → `truth_report.document_status`
6. fallback → `"failed"`

---

## 13. Knowledge Graph

Every `similarity_search` call in `extract_node` and `op_a_retry_node` writes one
`RetrievalLog` row per result (self-references skipped):

```python
RetrievalLog(
    document_id=<current>,
    retrieved_document_id=<similar>,
    stage="first_pass" | "retry",
    similarity_score=1 - cosine_distance,
)
```

`GET /knowledge-graph/?limit=50`:
- **Nodes:** most recent `limit` documents (id, filename, doc_type, status)
- **Edges:** `retrieval_logs` rows where both endpoints are in the node set
  (orphan edges excluded)

The frontend renders this as a force-directed graph (`streamlit-agraph`) with nodes
coloured by `doc_type` and edge width proportional to `similarity_score`.

---

## 14. Query and Explainability

### 14.1 Semantic Q&A (`POST /query/`)

```
query/retriever.py  similarity_search(query_embedding, top_k=5)
  └► list[dict]  (chunk_text, similarity_score, doc metadata)

query/synthesizer.py  synthesize(question, retrieved_docs)
  └► Gemini: "Answer using only these sources"
  → {answer: str, sources: list[dict]}
```

### 14.2 Semantic Search (`POST /search/`)

Same retrieval, no synthesis. Returns ranked results with similarity score and
300-character excerpt.

### 14.3 Timeline (`GET /documents/{id}/timeline`)

Ordered `confidence_logs` rows enriched with duration (difference between
consecutive timestamps) and retry labeling.

### 14.4 Explain (`GET /documents/{id}/explain`)

Human-readable verdict: verifier pass/fail summary, field coverage score, missing
required fields, and the learning action taken (embedded / skipped / corrected).

### 14.5 Analytics (`GET /analytics/`)

Aggregate metrics over all documents:
- Count by status
- HITL rate, retry rate, acceptance rate
- Average confidence by agent
- Verifier failure breakdown
- Resolution strategy usage distribution

---

## 15. Streamlit Dashboard

`frontend/app.py` — multipage entry point. All HTTP calls go through
`frontend/api_client.py`. No page imports `requests` directly.

`API_BASE_URL` (default `http://localhost:8000`; `http://app:8000` in Docker).

| File | Title | Key calls |
|---|---|---|
| `app.py` | Upload | `POST /ingest/` + live status poll via `GET /documents/{id}` |
| `pages/1_Documents.py` | Documents | `GET /documents/`, `/{id}`, `/{id}/timeline`, `/{id}/explain`, `/{id}/similar` |
| `pages/2_Search.py` | Search | `POST /search/` |
| `pages/3_Review_Queue.py` | Review Queue | `GET /review/pending`, `POST /review/{id}/decision` |
| `pages/4_Schema_Proposals.py` | Schema Proposals | `GET /schema-proposals/pending`, approve/reject |
| `pages/5_Analytics.py` | Analytics | `GET /analytics/` |
| `pages/6_Knowledge_Map.py` | Knowledge Map | `GET /knowledge-graph/` → `streamlit-agraph` |

**Design constraints:**
- All business logic stays in the API — dashboard is a pure presentation layer.
- `ApiError` caught per-page, rendered as `st.error()`, never unhandled.
- Dark theme via `frontend/.streamlit/config.toml`.
- `frontend/tests/test_smoke.py` — headless `AppTest` smoke tests for all 7 pages.

---

## 16. Data Model

### `documents`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `filename` | string | original upload name |
| `doc_type` | string | set at ingest; corrected by classify + write_output |
| `object_key` | string | `raw/<filename>` in object store |
| `hash` | string(64) | SHA-256 for dedup |
| `file_size` | int | |
| `mime_type` | string | |
| `status` | string | `queued` → terminal |
| `current_phase` | string | fine-grained pipeline phase |
| `universal_schema` | JSON | 3 canonical fields |
| `extracted_fields` | JSON | full doc-type-specific extraction |
| `created_at` | timestamp | |
| `updated_at` | timestamp | on-update |

**Phase progression:**
```
pending → ingested → classifying → extracting → evaluating → planning →
executing → retrying → awaiting_review → normalizing → finalizing →
completed | rejected | failed | persist_failed
```

### `confidence_logs`

One row per agent per document. `agent` ∈ `{classify, extract, truth_engine, schema_diff, persist}`.

`persist` writes `score=1.0` on success, `score=0.0` on `persist_failed`.

### `truth_audit_logs`

One row per pipeline run. Snapshot of `TruthReport`:
- `final_confidence`, `decision_reason`
- `coverage_score`, `required_fields_missing`, `additional_fields`
- `verification_reports` JSON array
- `document_status`, `allow_completion`, `allow_embedding`, `allow_learning`
- `verifier_version` (enables replay when verifier logic changes)

### `persistence_audit_logs`

One row per pipeline run. Snapshot of `ResolutionDecision` + `LearningDecision`:
- `resolution_strategy`, `resolution_reason`, `resolution_requires_human`
- `learning_candidate`, `allow_learning`, `learn_from_document`, `learn_from_correction`
- `schema_candidate`, `schema_proposal_json`
- `persist_status`, `persist_reason`

### `document_embeddings`

768-dim pgvector. One row per chunk (`chunk_index=0` = full `extracted_fields` JSON).
`source` ∈ `{"document", "hitl_correction"}`.

### `retrieval_logs`

| Column | Notes |
|---|---|
| `document_id` | the document being extracted |
| `retrieved_document_id` | the document used as RAG context |
| `stage` | `first_pass` or `retry` |
| `similarity_score` | `1 - cosine_distance` ∈ [0, 1] |

### `schema_versions`

Versioned schemas. Partial unique index ensures one `is_active=TRUE` per `doc_type`.
`source` ∈ `{"reference", "auto_discovered"}`.

### `schema_proposal_records`

| `status` | Transition |
|---|---|
| `pending` | initial; visible in `GET /schema-proposals/pending` |
| `approved` | `POST /schema-proposals/{id}/approve` → new SchemaVersion activated |
| `rejected` | `POST /schema-proposals/{id}/reject` → `rejection_reason` stored; never deleted |

### LangGraph checkpoint tables

Created by `PostgresSaver.setup()` (`make checkpointer`). Outside Alembic.
Stores serialised `GraphState` snapshots for interrupt/resume and crash recovery.

---

## 17. Configuration and Adapters

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `ENV` | `LOCAL` | `LOCAL` = MinIO + LocalWatch; `GCP` = GCS + Pub/Sub |
| `DATABASE_URL` | postgres://... | SQLAlchemy DSN |
| `MINIO_ENDPOINT` | `localhost:9000` | |
| `MINIO_BUCKET` | `documents` | |
| `GOOGLE_API_KEY` | — | Required for live LLM calls |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Override for a different model |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | |
| `EMBEDDING_DIMENSIONS` | `768` | Must match embedding model output |
| `CONFIDENCE_THRESHOLD` | `0.85` | Gate between auto-accept and retry |
| `MAX_RETRIES` | `2` | Attempts before HITL escalation |
| `REVIEW_API_KEY` | — | If set, gates HITL and schema-proposal write routes |
| `LANGCHAIN_API_KEY` | — | Optional LangSmith tracing |

### Adapter pattern

```
ENV=LOCAL  →  MinioStore   /  LocalWatchTrigger (inotify on /tmp/extract_it_watch)
ENV=GCP    →  GCSStore     /  PubSubTrigger (Cloud Pub/Sub subscription)
```

Both pairs share the same `ObjectStore` / `Trigger` base-class interfaces — zero
application-code changes between environments.

---

## 18. Startup Recovery

`api/main.py` `_recover_stranded_documents()` runs at every lifespan start:

1. Waits up to 30 s for the `documents` table (handles the race where `clear_data.sh`
   wipes and re-migrates the DB after the app container starts).
2. Queries documents with `status=queued AND current_phase IN _IN_PROGRESS_PHASES`.
3. Spawns one non-daemon thread per stranded document that calls `graph.invoke()`.

**Operational constraint:** uvicorn `--reload` (used in Docker Compose) kills all
background threads on any `.py` file edit via watchfiles. Avoid editing files while
a pipeline is running. The startup recovery mitigates container restart stranding
but cannot mitigate watchfiles kills mid-edit session.

---

## 19. Observability

**LangSmith** (optional): set `LANGCHAIN_API_KEY` + `LANGCHAIN_TRACING_V2=true`.
Every `graph.invoke()` emits a trace with node latency, LLM token counts, tool calls,
and the conditional edge taken. Missing key → harmless warning, non-blocking.

**Confidence logs:** one `ConfidenceLog` row per agent per run. Queryable via
`GET /documents/{id}` → `confidence_logs[]` or `GET /documents/{id}/timeline`.

**Phase tracking:** `Document.current_phase` stamped at each node entry — lightweight
in-DB timeline without a separate events table.

**Graph rendering (local dev):**
```python
from pipelines.graph import get_graph
png = get_graph().get_graph().draw_mermaid_png()
open("/tmp/graph.png", "wb").write(png)
```

---

## 20. CI Pipeline

`.github/workflows/ci.yml` — on every push to `main`:

```
push
  │
  ├── lint             ruff check, ruff format --check, mypy (Python 3.12)
  │
  ├── migrations       pgvector container; alembic upgrade→downgrade base→upgrade
  │
  ├── unit-tests       pytest tests/unit -m "not live"  (all I/O mocked)
  │
  └── integration-tests  (needs: lint + migrations + unit-tests)
                       testcontainers postgres; LLM + object store mocked
                       pytest tests/integration -m "not live"

e2e-tests: if: false   Gated until GCP deployment
```

Python version in CI: **3.12** (local dev: 3.11 — both supported).

---

## 21. Design Decisions

**Why LangGraph?**  
The pipeline needs state that survives across HTTP requests (HITL interrupt), retry
loops with shared counters, and conditional branching. LangGraph provides TypedDict
state, Postgres-backed checkpointing, and `interrupt()` for HITL, out of the box.

**Why Annotated reducers on GraphState?**  
LangGraph raises on conflicting writes to the same key without a reducer. `_keep_last`
lets `op_a_retry` overwrite `extracted_fields` each pass. `operator.add` accumulates
`validation_issues` and `execution_history` across all passes.

**Why `route_after_hitl` always returns `normalize`?**  
Universal schema must be computed for every document — approved or rejected — so
analytics and search remain consistent. The `persist` node reads `hitl_approved`
directly to set the correct terminal status.

**Why DB-first schema loading with YAML as bootstrap?**  
YAML can't be atomically versioned. Postgres gives atomic bumps, a partial unique
index (one active row per doc_type), and a clean audit trail. YAML is the
human-readable bootstrap, seeded once by Alembic.

**Why stamp phase before the node (not after)?**  
`write_output` sets `current_phase = status` as the terminal transition. Post-stamp
would overwrite this with `"finalizing"`. Pre-stamp is always overrideable by the
node's own DB writes.

**Why retrieval_logs instead of a synthetic similarity matrix?**  
A pairwise similarity plot is expensive and doesn't reflect actual usage. `retrieval_logs`
records only retrievals that actually influenced an extraction — the real causal edges.

**Why use the running `balance` column for balance arithmetic?**  
Summing debit/credit amounts double-counts "Opening Balance" marker rows that LLMs
often include as the first transaction entry. The last transaction's running `balance`
is the direct closing balance — no summation needed.

**Why write `doc_type` back in `write_output()`?**  
`ingest_file` parses `doc_type` from the filename pattern — many documents don't
follow it, leaving `doc_type=NULL`. The classify node determines the real `doc_type`
and puts it in pipeline state; `write_output` is the single place where all final
values are persisted, so `similarity_search` (which filters by `doc_type`) correctly
finds prior same-type documents for future RAG.

**Why `LearningPolicy` is the sole embedding authority?**  
Centralising the embedding decision prevents embeddings from being created in
`review.py`, `op_a_retry_node`, and `write_output` independently — three places that
would each need to track the same "has this already been embedded?" invariant.

---

## 22. Phase Index

| Phase | Scope | Status |
|---|---|---|
| [P0 — Scaffold](phase-p0-scaffold.md) | Contracts, TypedDict state, infra skeleton | ✅ Done |
| [P1 — Ingestion](phase-p1-ingestion.md) | MinIO, API, Document model, object store | ✅ Done |
| [P2 — Classification](phase-p2-classification.md) | classify_agent, routing engine, RegistryEntry | ✅ Done |
| [P3 — Extraction](phase-p3-extraction.md) | extract_agent, schema loader, Pydantic models | ✅ Done |
| [P4 — Truth Engine](phase-p4-truth-engine.md) | Deterministic verifiers, TruthReport, VerifierRegistry | ✅ Done |
| [P5 — Resolution Engine](phase-p5-resolution-engine.md) | ResolutionPlanner, StrategyExecutor, strategies | ✅ Done |
| [P5-HITL — Human-in-the-Loop](phase-p5-hitl.md) | op_b_hitl, checkpointer, review API, LearningPolicy | ✅ Done |
| [P6 — RAG Retry](phase-p6-rag-retry.md) | pgvector, op_a_retry, schema_diff_agent, retrieval logging | ✅ Done |
| [P7 — Query API](phase-p7-query.md) | Semantic Q&A, synthesizer, /query endpoint | ✅ Done |
| [P8 — Verifiers + CI](phase-p8-verifiers.md) | Self-consistency voting, deterministic verifiers, CI pipeline | ✅ Done |
| [P9 — Schema Versioning](phase-p9-schema-versioning.md) | schema_versions table, auto-discovery, 4 new doc types | ✅ Done |
| [P10 — Normalization](phase-p10-normalization.md) | normalize_node, universal_schema, date canonicalization | ✅ Done |
| [P11 — Persistence](phase-p11-persistence.md) | Atomic 4-phase write, PersistenceAuditLog, SchemaProposalRecord | ✅ Done |
| [P12 — Explainability](phase-p12-explainability.md) | search, similar, timeline, explain, analytics endpoints | ✅ Done |
| [P13 — Dashboard](phase-p13-dashboard.md) | 7-page Streamlit UI, api_client, dark theme, smoke tests | ✅ Done |
| [P14 — GCP Deployment](phase-p14-gcp.md) | Cloud Run, GCS, Cloud SQL, Pub/Sub | 🔲 Planned |
