# Starting the Project

## Prerequisites

- Docker Desktop running
- Python 3.11 (via pyenv or system)
- `pip install -e ".[dev]"` already run once for local tooling (pytest, ruff, mypy)

---

## First-time setup

### 1. Copy environment file

```bash
cp .env.example .env
```

Edit `.env` and fill in any keys you need:

| Key | Required for | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | Live LLM calls (classify, extract) | Leave empty to run mocked tests only |
| `LANGCHAIN_API_KEY` | LangSmith tracing | Optional — app works without it, emits a harmless warning |
| All others | Local infra | Defaults in `.env.example` work as-is |

### 2. Build the app image

Run this once, and again any time `pyproject.toml` changes:

```bash
docker compose build app
```

### 3. Start services

```bash
docker compose up -d
```

Wait ~10 seconds for postgres and minio to pass their healthchecks, then confirm:

```bash
docker compose ps
```

Expected output — postgres and minio show `(healthy)`, app and frontend show `Up`:

```
NAME                    STATUS
extract-it-app-1        Up
extract-it-frontend-1   Up
extract-it-minio-1      Up (healthy)
extract-it-postgres-1   Up (healthy)
```

### 4. Run database migrations

```bash
make migrate
```

This applies Alembic migrations (creates `documents`, `extraction_results`,
`confidence_logs`, `document_embeddings` tables) inside the running app container.

### 5. Initialise LangGraph checkpointer tables

These are separate from the Alembic migrations and must be run once:

```bash
docker compose exec app python -c "
from langgraph.checkpoint.postgres import PostgresSaver
from config.settings import settings
raw_url = settings.DATABASE_URL.replace('postgresql+psycopg://', 'postgresql://')
with PostgresSaver.from_conn_string(raw_url) as cp:
    cp.setup()
print('Checkpointer tables ready')
"
```

> **Note:** `db/checkpointer.py` currently passes the SQLAlchemy DSN prefix
> (`postgresql+psycopg://`) directly to `PostgresSaver`, which requires a raw
> `postgresql://` URI. The command above uses the corrected URL directly.
> This bug is tracked and will be fixed in P7.

### 6. Verify everything is up

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

## Day-to-day start (after first-time setup)

```bash
docker compose up -d
# wait ~10s
make migrate          # safe to re-run — Alembic is idempotent
```

No need to re-run the checkpointer setup or rebuild the image unless
`pyproject.toml` changed.

---

## Stopping

```bash
docker compose down        # stops containers, keeps volumes (data survives)
docker compose down -v     # stops containers AND deletes volumes (fresh state)
```

---

## Running tests

```bash
# Unit + integration, no live API calls (fast, ~5s)
make test

# All tests including live LLM calls (requires GOOGLE_API_KEY in .env)
make test-live
```

---

## Smoke tests (manual verification)

These scripts require running inside the app container and a document already
ingested (record the `document_id` from a POST to `/ingest/`).

```bash
# 4a — mocked node chain (no API cost)
docker compose exec app sh -c \
  "PYTHONPATH=/app python scripts/manual_pipeline_smoke.py --document-id <id> --mock"

# 4b — live node chain (requires GOOGLE_API_KEY)
docker compose exec app sh -c \
  "PYTHONPATH=/app python scripts/manual_pipeline_smoke.py --document-id <id>"

# Part 5 — HITL interrupt/resume via single-node graph
docker compose exec app sh -c \
  "PYTHONPATH=/app python scripts/manual_hitl_smoke.py --document-id <id>"
```

---

## Service endpoints

| Service | URL | Notes |
|---|---|---|
| FastAPI app | http://localhost:8000 | `/health`, `/ingest/`, `/review/` |
| API docs | http://localhost:8000/docs | Swagger UI |
| Streamlit UI | http://localhost:8501 | Ingest, Query (P8), HITL Review |
| MinIO console | http://localhost:9001 | Login: `minioadmin` / `minioadmin` |
| Postgres | `localhost:5432` | DB: `docint`, user: `user`, pass: `password` |

> Postgres on port 5432 conflicts with a local Postgres installation if one
> is running. `make migrate` and the checkpointer setup command both run
> inside the container so they are unaffected. Direct `psql` from the host
> should use `docker compose exec postgres psql -U user -d docint`.

---

## Current implementation status (P5 of 9)

| Phase | Scope | Status |
|---|---|---|
| P0 | Infrastructure scaffold | ✅ Done |
| P1 | Ingestion (MinIO, API, DB) | ✅ Done |
| P2 | Classify agent + LLM client | ✅ Done |
| P3 | Schema loader + Extract agent | ✅ Done |
| P4 | Validate agent + Router | ✅ Done |
| P5 | HITL node + Checkpointer + Review UI | ✅ Done |
| P6 | Normalize + op_a_retry nodes | 🔲 Pending |
| P7 | build_graph() + ingest→graph trigger | 🔲 Pending |
| P8 | RAG query (retriever + synthesizer) | 🔲 Pending |
| P9 | GCP deployment | 🔲 Pending |

Known gaps before P6: `POST /query/` and `POST /review/{id}/decision` are
non-functional stubs. The full pipeline (ingest → classify → extract →
validate → HITL → normalize) is not yet wired end-to-end; individual nodes
are verified via `scripts/manual_pipeline_smoke.py`.
