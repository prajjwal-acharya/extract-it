# doc-intel-platform

Autonomous document intelligence platform that ingests unstructured documents (PDFs, images), classifies them, extracts structured fields via a multi-agent LangGraph pipeline, validates output, and exposes results through a REST API and Streamlit HITL review UI.

## Architecture Overview

```
  File drop / API
        │
        ▼
┌───────────────────────────────────────────────────────────────────────┐
│  io_pipeline/  (ingestion layer)                                      │
│  • writes raw bytes → MinIO / GCS object store                        │
│  • creates Document row (status=queued) in Postgres                   │
│  • triggers LangGraph pipeline run                                    │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────────────┐
│  LangGraph pipeline  (pipelines/)                                     │
│                                                                       │
│  master ──▶ classify ──▶ extract ──▶ validate ──▶ route              │
│                                                      │                │
│                                          ┌───────────┴──────────┐    │
│                                          ▼                       ▼    │
│                                   op_a_retry              op_b_hitl   │
│                                   (RAG retry)             (human      │
│                                   ┌──────────┐             review)    │
│                                   │ schema   │                        │
│                                   │ diff     │                        │
│                                   │ agent    │                        │
│                                   │ + RAG    │                        │
│                                   │ context  │                        │
│                                   └────┬─────┘                        │
│                                        │                              │
│                                   re-extract ──▶ validate ──▶ route  │
│                                        │                              │
│                                        ▼                              │
│                                    normalize ──▶ write_output         │
│                                                                       │
│  Checkpointed in Postgres (langgraph-checkpoint-postgres)             │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │ writes
                              ▼
        ┌──────────────────────────────────────────┐
        │  PostgreSQL + pgvector                   │
        │  • documents, confidence_logs            │
        │  • document_embeddings (768-dim)         │
        │  • schema_versions (versioned schemas)   │
        │  • LangGraph checkpoint tables           │
        └─────────────┬──────┬──────────────────────┘
                      │      │
           ┌──────────┘      └──────────┐
           ▼                            ▼
  ┌────────────────┐          ┌──────────────────┐
  │  MinIO / GCS   │          │  LangSmith       │
  │  object store  │          │  (tracing)       │
  └────────────────┘          └──────────────────┘
                              
                              ▼
┌───────────────────────────────────────────────────────────────────────┐
│  FastAPI  (api/)                                                      │
│  POST /ingest          — upload a document, trigger pipeline          │
│  POST /query/          — semantic question answering over extractions │
│  POST /review/{id}/decision — HITL approve / reject with corrections │
└─────────────────────────────┬─────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────────────┐
│  Streamlit UI  (frontend/)                                            │
│  Upload ▸ pipeline status ▸ HITL review ▸ approve / reject           │
└───────────────────────────────────────────────────────────────────────┘
```

## Agent Stack

```
agents/
├── classify_agent.py      — doc_type + confidence via Gemini
├── extract_agent.py       — field extraction with self-consistency voting
│                            _extract_once() leaf  +  extract() orchestrator
│                            verifier tool-call loop (mrz_checksum, balance_arithmetic)
├── self_consistency.py    — 3-sample vote for confidence band [0.60, 0.85)
├── schema_diff_agent.py   — free-form field discovery + fuzzy diff + version bump
├── validate_agent.py      — rule-based field validation
├── verifiers.py           — deterministic: MRZ check digit, balance arithmetic
└── llm_client.py          — Gemini wrapper: generate(), embed(), generate_with_tools()
```

## Schema Versioning

```
config/schemas/<doc_type>.yaml   ← static bootstrap (never mutated)
        │ seeded by Alembic migration on first deploy
        ▼
schema_versions (Postgres)       ← source of truth once seeded
  doc_type | version | fields_json | is_active
  ─────────────────────────────────────────────
  passport  | 1.0    | [...]       | TRUE   ← partial unique index enforces
  passport  | 1.1    | [...]       | FALSE    exactly one active row per doc_type
```

`op_a_retry` runs `schema_diff_agent` before every RAG re-extraction:
1. Free-form Gemini extraction (no `response_schema`) discovers all visible field labels
2. Fuzzy match (SequenceMatcher ≥ 0.82) against active schema's scalar fields
3. New labels → `additions`; required scalar fields absent from discovery → `relaxed_fields`
4. If diff non-empty: atomically bump version, flip `is_active` — extraction picks up the new schema on the same request

Array-type fields (e.g. `transactions`) are excluded from diff/relaxation. Nested item-level schema evolution is deferred.

## Supported Doc Types

| doc_type | Fields | Notes |
|---|---|---|
| `passport` | 11 | MRZ checksum verifier |
| `bank_statement` | 8 scalar + transactions array | Balance arithmetic verifier |
| `gst_invoice` | 11 | GSTIN + tax breakdown |
| `salary_slip` | 11 | PAN, UAN, net pay |
| `itr` | 8 | PAN, assessment year, acknowledgement |
| `property_deed` | 7 | Executant/claimant, registration date |

## Key Design Decisions

- **LangGraph TypedDict state** with `Annotated` reducers (`operator.add`, `_keep_last`) on fields written by concurrent or retry nodes.
- **Self-consistency voting**: borderline extractions (confidence ∈ [0.60, 0.85)) run 3 passes; per-field mode vote resolves disagreements.
- **Deterministic verifiers**: MRZ check digits and balance arithmetic are computed in Python, not inferred by the LLM — result is `verification_passed: bool | None` logged to `ConfidenceLog`.
- **DB-first schema loading**: `load_schema_model()` queries `schema_versions` first; YAML is the bootstrap fallback only. Cache key is the version string — auto-invalidated on schema bump.
- **Adapter pattern** (`adapters/`): MinIO (local) and GCS (GCP) swapped via `ENV=LOCAL|GCP`.
- **Confidence threshold** (`CONFIDENCE_THRESHOLD=0.85`) gates auto-normalization vs. RAG retry vs. HITL escalation.
- **HITL exemplar embedding**: approved corrections are embedded and stored in `document_embeddings` to improve future RAG context.
- **API key guard on review route**: `REVIEW_API_KEY` env var gates `POST /review`; unset = open (dev mode).

## Local Quick-Start

```bash
cp .env.example .env          # fill in GEMINI_API_KEY, REVIEW_API_KEY (optional)
make up                        # docker compose up -d (Postgres + pgvector + MinIO)
make migrate                   # alembic upgrade head
```

Services after `make up`:

| Service    | URL                       |
|------------|---------------------------|
| API        | http://localhost:8000      |
| API docs   | http://localhost:8000/docs |
| MinIO UI   | http://localhost:9001      |
| Frontend   | http://localhost:8501      |

## CI

GitHub Actions (`.github/workflows/ci.yml`):

| Job | Trigger | What it does |
|---|---|---|
| `lint` | every push | `ruff check`, `ruff format --check`, `mypy` |
| `migrations` | every push | pgvector container; alembic round-trip upgrade→downgrade→upgrade |
| `unit-tests` | every push | `pytest tests/unit/ -m "not live"` — all deps mocked |
| `integration-tests` | after lint+migrations+unit | pgvector container; mocks LLM/embed |
| `e2e-tests` | `if: false` | gated until GCP deployment is live |

## Phase Roadmap

| Phase | Status | Scope |
|---|---|---|
| P0 | done | Scaffold, contracts, TypedDict state |
| P1 | done | Ingestion pipeline, object store, Document model |
| P2 | done | Classification agent |
| P3 | done | Field extraction agent |
| P4 | done | Validation agent |
| P5 | done | HITL review UI (Streamlit) |
| P6 | done | Normalization, universal schema, output writing |
| P7 | done | RAG retry with pgvector, compiled LangGraph |
| P8 | done | Query API, semantic synthesizer, embedding task-type asymmetry |
| P9 | done | Verifier tools, self-consistency voting, CI pipeline |
| P10 | done | Schema versioning, auto-discovery agent, 4 new doc_types |
| P11 | planned | GCP deployment (Cloud Run, GCS, Cloud SQL) |
