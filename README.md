# extract-it

Autonomous document intelligence platform. Ingests unstructured documents (PDFs,
images), classifies them, extracts structured fields via a multi-agent LangGraph
pipeline with deterministic verification, validates output, and exposes results
through a REST API and a 7-page Streamlit dashboard with HITL review.

→ Full architecture with data-flow diagrams and design decisions: [architecture/architecture.md](architecture/architecture.md)  
→ Step-by-step setup and daily operations: [STARTUP.md](STARTUP.md)  
→ Per-phase architecture docs: [architecture/](architecture/)

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
| Dashboard | http://localhost:8501 | 7-page Streamlit app |
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
| `GET` | `/documents/{id}/explain` | — | Human-readable explanation: verifiers, field coverage, learning action |
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

Actual graph topology (from `pipelines/graph.py`):

```
master → classify ──[route]──► extract         (PROCEED: confidence ≥ 0.70)
                             ► unknown_handler → persist → END

extract → truth_engine → resolution_planner → strategy_executor
                                                      │
              ┌──────────────────────────────────────┤
          ACCEPT                            RETRY / PROMPT_REFINEMENT /    HITL
       (normalize)                          BETTER_RETRIEVAL /        (op_b_hitl)
                                            IMAGE_PREPROCESS /
                                            MODEL_ESCALATION
                                            (op_a_retry → truth_engine ← retry loop)

                        REJECT → persist (skips normalize)

op_b_hitl ──► normalize  (always — both approve and reject)
normalize → persist → END
```

**Atomic persist** (`write_output`) is a 4-phase write:

```
Phase A: DB audit rows (ConfidenceLog, TruthAuditLog, PersistenceAuditLog, SchemaProposalRecord)
Phase B: Object store (MinIO / GCS)
Phase C: Embedding (only if LearningPolicy.allow_learning)
Phase D: Terminal status (completed | rejected | verification_failed | failed)

Any failure → rollback + status = persist_failed + score-0.0 ConfidenceLog
```

Every node stamps `Document.current_phase` so progress is observable in real time.

## Dashboard Pages

| Page | File | What it does |
|---|---|---|
| Upload | `app.py` | File upload + live status polling |
| Documents | `pages/1_Documents.py` | Browse all docs; tabs for Overview / Timeline / Explain / Similar |
| Search | `pages/2_Search.py` | Semantic search with similarity scores and excerpts |
| Review Queue | `pages/3_Review_Queue.py` | HITL approve / reject with per-field correction editor |
| Schema Proposals | `pages/4_Schema_Proposals.py` | Approve or reject pending schema changes |
| Analytics | `pages/5_Analytics.py` | Bar charts: strategy usage, verifier failures, avg confidence |
| Knowledge Map | `pages/6_Knowledge_Map.py` | Force-directed retrieval graph (`streamlit-agraph`) |

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
├── validate_agent.py      legacy rule-based validation (retained for compatibility)
├── verifiers.py           MRZ, balance arithmetic, GSTIN, PAN, AY/FY, deed dates, etc.
└── llm_client.py          Gemini: generate / embed / generate_with_tools
```

## Supported Document Types

| doc_type | Key fields | Verifiers |
|---|---|---|
| `passport` | surname, given_names, nationality, DOB, sex, place_of_birth, issue/expiry dates, passport_number, mrz_line1/2 | mrz_checksum, passport_date_consistency |
| `bank_statement` | account_holder, account_number/iban, bank_name, opening/closing_balance, statement_period_start/end, currency, transactions[] | balance_arithmetic, statement_period_ordering |
| `driving_license` | full_name, license_number, DOB, issue/expiry dates, address, vehicle_classes | — |
| `aadhaar` | aadhaar_number, full_name, DOB, gender, address, vid | — |
| `gst_invoice` | gstin, invoice_number, invoice_date, seller/buyer, HSN, tax_breakdown, totals | gstin_checksum, invoice_total_consistency |
| `salary_slip` | employee_name, PAN, UAN, employer, pay_period, basic/allowances/deductions, net_pay | gross_consistency, pan_validation |
| `itr` | PAN, assessment_year, financial_year, ITR_form, gross_income, tax_paid, refund, acknowledgement | pan_validation, ay_fy_consistency |
| `property_deed` | deed_type, executant, claimant, property_description, area, consideration, execution/registration dates | deed_date_consistency |

## Schema System

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

Schema proposals go to `schema_proposal_records` as `pending`. Human approval via
`POST /schema-proposals/{id}/approve` activates the new SchemaVersion.

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
| P1 | ✅ done | Ingestion pipeline, object store, Document model, dedup |
| P2 | ✅ done | Classification agent + routing engine + DocumentRegistry |
| P3 | ✅ done | Field extraction agent + schema loader + self-consistency |
| P4 | ✅ done | Truth Engine (deterministic verifiers, TruthReport, VerifierRegistry) |
| P5 | ✅ done | Resolution Engine (ResolutionPlanner, StrategyExecutor, strategies) |
| P5-HITL | ✅ done | Human-in-the-loop: op_b_hitl, checkpointer, review UI, LearningPolicy |
| P6 | ✅ done | RAG retry with pgvector, schema_diff_agent, retrieval logging |
| P7 | ✅ done | Query API, semantic synthesizer, embedding task-type asymmetry |
| P8 | ✅ done | Deterministic verifiers, self-consistency voting, CI pipeline |
| P9 | ✅ done | Schema versioning, auto-discovery, 4 new doc_types |
| P10 | ✅ done | Normalization, universal schema, date canonicalization, fallback mapping |
| P11 | ✅ done | Atomic 4-phase persist, PersistenceAuditLog, SchemaProposalRecord, schema proposals API |
| P12 | ✅ done | Query & Explainability API (search, similar, timeline, explain, analytics) |
| P13 | ✅ done | 7-page Streamlit dashboard (api_client, dark theme, smoke tests) |
| P14 | 🔲 planned | GCP deployment (Cloud Run, GCS, Cloud SQL, Pub/Sub) |
