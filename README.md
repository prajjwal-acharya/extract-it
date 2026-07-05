# doc-intel-platform

Autonomous document intelligence platform that ingests unstructured documents
(PDFs, images), classifies them, extracts structured fields via a multi-agent
LangGraph pipeline, validates output with deterministic verifiers, and exposes
results through a REST API and a multipage Streamlit dashboard with HITL review.

→ Full architecture with data-flow diagrams and design decisions: [ARCHITECTURE.md](ARCHITECTURE.md)  
→ Step-by-step setup and daily operations: [STARTUP.md](STARTUP.md)

## Quick-start

```bash
cp .env.example .env          # fill in GOOGLE_API_KEY; everything else has safe defaults
make up                       # docker compose up -d --build
make migrate                  # alembic upgrade head (idempotent)
make checkpointer             # one-time: initialise LangGraph checkpointer tables
```

Open the dashboard at **http://localhost:8501** and the API docs at **http://localhost:8000/docs**.

## Services

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000 | REST endpoints |
| API docs | http://localhost:8000/docs | Swagger UI (auto-generated) |
| Dashboard | http://localhost:8501 | Multipage Streamlit app |
| MinIO console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| Postgres | `localhost:5432` | DB `docint`, user `user`, pass `password` |

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/health` | — | Liveness probe |
| `POST` | `/ingest/` | — | Upload a document; pipeline runs async |
| `GET` | `/documents/` | — | List documents (filter: `status`, `doc_type`; paginate: `limit`, `offset`) |
| `GET` | `/documents/{id}` | — | Full detail: extracted fields, truth report, resolution, learning, persistence audit |
| `GET` | `/documents/{id}/references` | — | RAG retrieval edges for this document |
| `GET` | `/documents/{id}/similar` | — | Top-k semantically similar documents (pgvector cosine) |
| `GET` | `/documents/{id}/timeline` | — | Ordered execution events with timestamps, duration, and retry labeling |
| `GET` | `/documents/{id}/explain` | — | Human-readable extraction explanation: verifiers, field coverage, learning action |
| `GET` | `/knowledge-graph/` | — | Node/edge graph payload for the most recent `limit` documents |
| `POST` | `/search/` | — | Semantic search: `{query, doc_type?, top_k}` → ranked results with excerpts |
| `GET` | `/analytics/` | — | Aggregate metrics: acceptance rate, HITL rate, retry rate, verifier failures, avg confidence |
| `GET` | `/review/pending` | — | Documents awaiting human review |
| `POST` | `/review/{id}/decision` | `X-API-Key` | Resume HITL: `{approved, corrections}` |
| `GET` | `/schema-proposals/pending` | — | Schema proposals awaiting human approval |
| `POST` | `/schema-proposals/{id}/approve` | `X-API-Key` | Approve a schema proposal → activates new SchemaVersion |
| `POST` | `/schema-proposals/{id}/reject` | `X-API-Key` | Reject a schema proposal (stores reason; auditable) |
| `POST` | `/query/` | — | Semantic Q&A over extracted document corpus |

## Pipeline

```
master → classify → extract → validate
                                  │
              confidence ≥ 0.85 ──► normalize → persist (atomic) → END
              retry available   ──► op_a_retry (schema diff + RAG) → validate
              retries exhausted ──► op_b_hitl (human review) → normalize | persist
```

**Atomic persist** (`write_output`) is a 4-phase write:

```
Phase A: DB audit rows (ConfidenceLog, TruthAuditLog, PersistenceAuditLog, SchemaProposalRecord)
Phase B: Object store (MinIO / GCS)
Phase C: Embedding (only if LearningPolicy.allow_learning)
Phase D: Terminal status (completed | rejected | verification_failed)

Any failure → rollback + status = persist_failed + score-0.0 ConfidenceLog
```

Every node stamps `Document.current_phase` so progress is observable in real time.

## Dashboard Pages

| Page | Path | What it does |
|---|---|---|
| Upload | `/` (`app.py`) | File upload + live status polling |
| Documents | `pages/1_📋_Documents.py` | Browse all docs; tabs for Overview / Timeline / Explain / Similar |
| Search | `pages/2_🔍_Search.py` | Semantic search with similarity scores and excerpts |
| Review Queue | `pages/3_✅_Review_Queue.py` | HITL approve / reject with per-field correction editor |
| Schema Proposals | `pages/4_🏛_Schema_Proposals.py` | Approve or reject pending schema changes |
| Analytics | `pages/5_📊_Analytics.py` | Bar charts: strategy usage, verifier failures, avg confidence |
| Knowledge Map | `pages/6_🗺_Knowledge_Map.py` | Force-directed retrieval graph (`streamlit-agraph`) |

Run locally (outside Docker):
```bash
make dashboard    # API_BASE_URL=http://localhost:8000 streamlit run frontend/app.py
```

## Agent Stack

```
agents/
├── classify_agent.py      doc_type + confidence via Gemini
├── extract_agent.py       structured field extraction
│                          └─ self-consistency voting (confidence 0.60–0.85: 3-sample vote)
│                          └─ deterministic verifier tool loop (MRZ, balance arithmetic)
├── schema_diff_agent.py   free-form field discovery → fuzzy diff → version bump
├── validate_agent.py      rule-based field validation
├── verifiers.py           MRZ check digit (ICAO 9303), balance arithmetic
└── llm_client.py          Gemini: generate / embed / generate_with_tools
```

## Schema Versioning

```
config/schemas/<doc_type>.yaml   ← static bootstrap (never mutated at runtime)
        │ seeded by Alembic on first deploy
        ▼
schema_versions (Postgres)       ← live source of truth
  doc_type | version | fields_json | is_active
  passport  | 1.0    | [...]       | FALSE   ← superseded
  passport  | 1.1    | [...]       | TRUE    ← active (partial unique index)
```

`op_a_retry` runs `schema_diff_agent` before every RAG re-extraction:
1. Loose Gemini extraction (no `response_schema`) discovers all visible field labels
2. Fuzzy match (SequenceMatcher ≥ 0.82) against active schema's scalar fields
3. New labels → `additions`; required fields absent from document → `relaxed_fields`
4. Non-empty diff → atomic version bump; extraction picks up the new schema immediately

Schema proposals from the pipeline go to `schema_proposal_records` with status `pending`.
Human approval via `POST /schema-proposals/{id}/approve` activates the new SchemaVersion.

## Supported Doc Types

| doc_type | Fields | Verifier |
|---|---|---|
| `passport` | 11 (surname, given_names, nationality, DOB, sex, place_of_birth, issue/expiry dates, passport_number, mrz_line1/2) | MRZ check digit |
| `bank_statement` | 8 scalar + transactions array | Balance arithmetic |
| `gst_invoice` | 11 (GSTIN, invoice number, date, seller/buyer, HSN, tax breakdown, totals) | — |
| `salary_slip` | 11 (employee name, PAN, UAN, employer, period, basic/allowances/deductions, net pay) | — |
| `itr` | 8 (PAN, assessment year, ITR form, gross income, tax paid, refund, acknowledgement) | — |
| `property_deed` | 7 (deed type, executant, claimant, property description, area, consideration, registration date) | — |

## Key Design Decisions

- **LangGraph TypedDict state** with `Annotated` reducers (`operator.add`, `_keep_last`) on fields written by concurrent or retry nodes
- **Self-consistency voting**: borderline extractions (confidence ∈ [0.60, 0.85)) run 3 passes; per-field mode vote resolves disagreements
- **Deterministic verifiers**: MRZ check digits and balance arithmetic computed in Python, not inferred — logged as `verification_passed: bool | None`
- **Atomic 4-phase persist**: any failure after Phase A → `persist_failed` status; document never shows `completed` unless all writes succeeded
- **LearningPolicy as sole embedding authority**: `write_output` decides whether and how to embed; `review.py` does not embed directly
- **SchemaProposalRecord**: pipeline-generated schema changes go to DB as `pending`; human approval via API activates them
- **DB-first schema loading**: `load_schema_model()` queries `schema_versions` first; YAML is the bootstrap-only fallback; cache key is the version string
- **Retrieval logging**: every RAG retrieval event writes a `RetrievalLog` row — real causal edges, not a synthetic similarity matrix
- **Adapter pattern** (`adapters/`): MinIO / LocalWatch (local) and GCS / Pub/Sub (GCP) swapped via `ENV=LOCAL|GCP`; zero application-code changes

## CI

`.github/workflows/ci.yml` — runs on every push to `main`:

| Job | Depends on | What it does |
|---|---|---|
| `lint` | — | `ruff check`, `ruff format --check`, `mypy` (Python 3.12) |
| `migrations` | — | pgvector container; alembic round-trip upgrade → downgrade base → upgrade |
| `unit-tests` | — | `pytest tests/unit -m "not live"` — all I/O mocked |
| `integration-tests` | lint + migrations + unit-tests | testcontainers postgres; LLM mocked |
| `e2e-tests` | — | `if: false` — gated until GCP deployment |

## Phase Roadmap

| Phase | Status | Scope |
|---|---|---|
| P0 | ✅ done | Scaffold, contracts, TypedDict state |
| P1 | ✅ done | Ingestion pipeline, object store, Document model |
| P2 | ✅ done | Classification agent + routing engine |
| P3 | ✅ done | Field extraction agent + schema loader |
| P4 | ✅ done | Validation agent + router |
| P5 | ✅ done | HITL node + checkpointer + review UI |
| P5.5 | ✅ done | Human collaboration, LearningPolicy, schema proposals |
| P6 | ✅ done | RAG retry with pgvector, compiled LangGraph, end-to-end wiring |
| P7 | ✅ done | Query API, semantic synthesizer, embedding task-type asymmetry |
| P8 | ✅ done | Deterministic verifiers, self-consistency voting, CI pipeline |
| P9 | ✅ done | Schema versioning, auto-discovery agent, 4 new doc_types |
| P10 | ✅ done | Normalization, universal schema, output writing |
| P11 | ✅ done | Transactional persistence, atomic 4-phase write, `persist_failed`, PersistenceAuditLog, SchemaProposalRecord, schema proposals API |
| P12 | ✅ done | Query & Explainability API (search, similar, timeline, explain, analytics) |
| P13 | ✅ done | Multipage Streamlit dashboard (7 pages, api_client, dark theme, smoke tests) |
| P14 | planned | GCP deployment (Cloud Run, GCS, Cloud SQL, Pub/Sub) |
