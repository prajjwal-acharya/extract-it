# doc-intel-platform

Autonomous document intelligence platform that ingests unstructured documents (PDFs, images), classifies them, extracts structured fields via a multi-agent LangGraph pipeline, validates output, and exposes results through a REST API and Streamlit HITL review UI.

## Architecture Overview

```
                          ┌──────────────────────────────────────────────┐
  File drop / API ───────▶│  io_pipeline (ingestion)                     │
                          │  • writes raw bytes to object store           │
                          │  • creates Document row (status=queued)       │
                          └────────────────┬─────────────────────────────┘
                                           │ triggers
                                           ▼
                          ┌──────────────────────────────────────────────┐
                          │  LangGraph pipeline  (pipelines/)            │
                          │                                              │
                          │  master ──▶ [classify ‖ extract] ──▶ validate│
                          │               ↓ parallel fan-out              │
                          │  validate ──▶ route ──▶ normalize             │
                          │                  └──▶ op_a_retry (RAG retry) │
                          │                  └──▶ op_b_hitl  (human)     │
                          │                                              │
                          │  Checkpointed in Postgres via               │
                          │  langgraph-checkpoint-postgres               │
                          └────────────────┬─────────────────────────────┘
                                           │ writes
                                           ▼
              ┌────────────────┐    ┌────────────────┐    ┌──────────────┐
              │  PostgreSQL    │    │  MinIO / GCS   │    │  LangSmith   │
              │  + pgvector    │    │  (object store)│    │  (tracing)   │
              └────────────────┘    └────────────────┘    └──────────────┘
                                           │
                          ┌────────────────┴─────────────────────────────┐
                          │  FastAPI  (api/)                             │
                          │  POST /ingest   GET /query                   │
                          └────────────────┬─────────────────────────────┘
                                           │
                          ┌────────────────┴─────────────────────────────┐
                          │  Streamlit UI  (frontend/)                   │
                          │  Upload ▸ review ▸ HITL approve / reject     │
                          └──────────────────────────────────────────────┘
```

## Key Design Decisions

- **LangGraph TypedDict state** with `Annotated` reducers on fields written in the `master → [classify ‖ extract]` parallel fan-out to avoid update-conflict errors.
- **pgvector** in Postgres for semantic similarity search used by the RAG retry path.
- **Adapter pattern** (`adapters/`) lets MinIO (local) and GCS (P9) be swapped via `ENV=LOCAL|GCP` without changing pipeline code.
- **Confidence threshold** (`CONFIDENCE_THRESHOLD=0.85`) gates automatic normalization vs. retry vs. HITL escalation.

## Local Quick-Start

```bash
cp .env.example .env          # fill in LANGCHAIN_API_KEY and GEMINI API key
make up                        # docker compose up -d
make migrate                   # alembic upgrade head
```

Services after `make up`:

| Service    | URL                        |
|------------|----------------------------|
| API        | http://localhost:8000       |
| API docs   | http://localhost:8000/docs  |
| MinIO UI   | http://localhost:9001       |
| Frontend   | http://localhost:8501       |

## Phase Roadmap

| Phase | Scope |
|-------|-------|
| P0    | Scaffold & contracts (this phase) |
| P1    | Ingestion pipeline |
| P2    | Document classification agent |
| P3    | Field extraction agent |
| P4    | Validation agent |
| P5    | HITL review UI |
| P6    | Normalization & output writing |
| P7    | RAG retry with pgvector |
| P8    | Query API & synthesizer |
| P9    | GCP deployment |
