# doc-intel-platform

Autonomous document intelligence platform that ingests unstructured documents
(PDFs, images), classifies them, extracts structured fields via a multi-agent
LangGraph pipeline, validates output, and exposes results through a REST API
and Streamlit HITL review UI.

→ For the full architecture with data-flow diagrams, component details, and
  design decisions see [ARCHITECTURE.md](ARCHITECTURE.md).

## Quick-start

```bash
cp .env.example .env          # fill in GOOGLE_API_KEY; everything else has safe defaults
make up                        # docker compose up -d --build
make migrate                   # alembic upgrade head (idempotent)
# one-time: initialise LangGraph checkpointer tables
docker compose exec app python -c "
from langgraph.checkpoint.postgres import PostgresSaver
from config.settings import settings
raw_url = settings.DATABASE_URL.replace('postgresql+psycopg://', 'postgresql://')
with PostgresSaver.from_conn_string(raw_url) as cp:
    cp.setup()
print('ok')
"
```

## Services

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000 | REST endpoints |
| API docs | http://localhost:8000/docs | Swagger UI |
| Streamlit UI | http://localhost:8501 | Ingest · Documents · Knowledge Map · HITL · Query |
| MinIO console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| Postgres | `localhost:5432` | DB `docint`, user `user`, pass `password` |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `POST` | `/ingest/` | Upload a document; pipeline runs async |
| `GET` | `/documents/` | List documents (filter: `status`, `doc_type`; paginate: `limit`, `offset`) |
| `GET` | `/documents/{id}` | Full detail: `extracted_fields`, `universal_schema`, `confidence_logs` |
| `GET` | `/documents/{id}/references` | RAG retrieval edges for this document |
| `GET` | `/knowledge-graph/` | Node/edge graph payload for the most recent `limit` documents |
| `GET` | `/review/pending` | Documents awaiting human review, with retry references |
| `POST` | `/review/{id}/decision` | Resume HITL: `{approved, corrections}` |
| `POST` | `/query/` | Semantic Q&A over extracted document corpus |

## Pipeline

```
master → classify → extract → validate
                                  │
              confidence ≥ 0.85 ──► normalize → persist → END
              retry available   ──► op_a_retry (schema diff + RAG) → validate
              retries exhausted ──► op_b_hitl (human review) → normalize | persist
```

Every node stamps `Document.current_phase` so progress is observable in real time
via `GET /documents/{id}` or the Streamlit Documents panel.

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

- **LangGraph TypedDict state** with `Annotated` reducers (`operator.add`, `_keep_last`)
  on fields written by concurrent or retry nodes
- **Self-consistency voting**: borderline extractions (confidence ∈ [0.60, 0.85))
  run 3 passes; per-field mode vote resolves disagreements
- **Deterministic verifiers**: MRZ check digits and balance arithmetic computed in
  Python, not inferred — result logged as `verification_passed: bool | None`
- **DB-first schema loading**: `load_schema_model()` queries `schema_versions` first;
  YAML is the bootstrap-only fallback; cache key is the version string
- **Retrieval logging**: every RAG retrieval event writes a `RetrievalLog` row —
  real causal edges, not a synthetic similarity matrix
- **Phase stamping before node execution**: wrapper stamps `current_phase` before
  calling the node so `write_output()`'s terminal phase assignment wins on persist
- **doc_type written back on completion**: `write_output()` persists the
  classified `doc_type` to the DB so future `similarity_search` filters work
  even for documents whose filename didn't match the ingestion-time regex
- **Adapter pattern** (`adapters/`): MinIO / LocalWatch (local) and GCS / Pub/Sub
  (GCP) swapped via `ENV=LOCAL|GCP`; zero application-code changes between envs
- **API key guard**: `REVIEW_API_KEY` gates `POST /review/{id}/decision`; unset = open

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
| P2 | ✅ done | Classification agent |
| P3 | ✅ done | Field extraction agent |
| P4 | ✅ done | Validation agent + router |
| P5 | ✅ done | HITL node + checkpointer + review UI |
| P6 | ✅ done | Normalization, universal schema, output writing |
| P7 | ✅ done | RAG retry with pgvector, compiled LangGraph, end-to-end wiring |
| P8 | ✅ done | Query API, semantic synthesizer, embedding task-type asymmetry |
| P9 | ✅ done | Deterministic verifiers, self-consistency voting, CI pipeline |
| P10 | ✅ done | Schema versioning, auto-discovery agent, 4 new doc_types |
| P11 | ✅ done | Documents dashboard, knowledge graph, HITL queue, phase tracker |
| P12 | planned | GCP deployment (Cloud Run, GCS, Cloud SQL, Pub/Sub) |
