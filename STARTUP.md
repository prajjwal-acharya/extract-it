# Starting the Project

## Prerequisites

- Docker Desktop running
- Python 3.11+ (via pyenv or system)
- `pip install -e ".[dev]"` run once for local tooling (pytest, ruff, mypy)

---

## First-time setup

### 1. Copy environment file

```bash
cp .env.example .env
```

Edit `.env` and fill in the keys you need:

| Key | Required for | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | Live LLM calls (classify, extract, embed) | Leave empty to run mocked tests only |
| `LANGCHAIN_API_KEY` | LangSmith tracing | Optional — app works without it, emits a harmless warning |
| `REVIEW_API_KEY` | HITL review auth | Optional — if unset the review route is open (dev mode) |
| All others | Local infra | Defaults in `.env.example` work as-is |

### 2. Build images

Run once, and again whenever `pyproject.toml` changes:

```bash
docker compose build app frontend
```

### 3. Start services

```bash
docker compose up -d
```

Wait ~10 seconds for postgres and minio healthchecks, then confirm:

```bash
docker compose ps
```

Expected:

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

Applies all Alembic migrations in order. Safe to re-run — idempotent.

Tables created: `documents`, `confidence_logs`, `document_embeddings`,
`schema_versions`, `retrieval_logs`. Also seeds all doc-type schemas from
`config/schemas/*.yaml`.

### 5. Initialise LangGraph checkpointer tables

These are separate from Alembic and must be run once per fresh database:

```bash
docker compose exec app python -c "
from langgraph.checkpoint.postgres import PostgresSaver
from config.settings import settings
raw_url = settings.DATABASE_URL.replace('postgresql+psycopg://', 'postgresql://')
with PostgresSaver.from_conn_string(raw_url) as cp:
    cp.setup()
print('ok')
"
```

### 6. Verify

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

---

## Day-to-day start

```bash
docker compose up -d
sleep 10
make migrate          # safe to re-run
```

No need to rebuild or re-run checkpointer setup unless `pyproject.toml` changed
or you wiped volumes with `docker compose down -v`.

---

## Stopping

```bash
docker compose down        # stops containers, keeps volumes (data survives)
docker compose down -v     # stops containers AND deletes volumes (clean slate)
```

---

## Running tests

```bash
# Unit + integration, no live API calls (fast, all I/O mocked)
make test

# All tests including live LLM calls (requires GOOGLE_API_KEY)
make test-live
```

The unit tests run without Docker. Integration tests use testcontainers to spin
up a real Postgres — requires Docker Desktop running.

---

## Smoke tests (manual verification)

These run inside the app container. Get a `document_id` first by posting a file
to `POST /ingest/`.

```bash
# Mocked node chain (no API cost)
docker compose exec app sh -c \
  "PYTHONPATH=/app python scripts/manual_pipeline_smoke.py --document-id <id> --mock"

# Live node chain (requires GOOGLE_API_KEY)
docker compose exec app sh -c \
  "PYTHONPATH=/app python scripts/manual_pipeline_smoke.py --document-id <id>"

# HITL interrupt/resume
docker compose exec app sh -c \
  "PYTHONPATH=/app python scripts/manual_hitl_smoke.py --document-id <id>"
```

---

## Visualise the LangGraph topology

```bash
docker compose exec app python -c "
from pipelines.graph import get_graph
png = get_graph().get_graph().draw_mermaid_png()
open('/tmp/graph.png', 'wb').write(png)
print('saved /tmp/graph.png')
"
docker cp extract-it-app-1:/tmp/graph.png ./graph.png
open graph.png
```

Or print Mermaid text and paste into https://mermaid.live:

```bash
docker compose exec app python -c "
from pipelines.graph import get_graph
print(get_graph().get_graph().draw_mermaid())
"
```

---

## Service endpoints

| Service | URL | Notes |
|---|---|---|
| FastAPI app | http://localhost:8000 | All REST endpoints |
| API docs | http://localhost:8000/docs | Swagger UI |
| Streamlit UI | http://localhost:8501 | Ingest · Documents · Knowledge Map · HITL Queue · Query |
| MinIO console | http://localhost:9001 | Login: `minioadmin` / `minioadmin` |
| Postgres | `localhost:5432` | DB: `docint`, user: `user`, pass: `password` |

> **Port conflict**: if you have a local Postgres on 5432, `make migrate` and
> the checkpointer command are unaffected (they run inside the container).
> For direct `psql` access from the host use:
> `docker compose exec postgres psql -U user -d docint`

---

## GCP simulation (local)

To test the GCP adapters (GCS object store, Pub/Sub trigger) without a live GCP
project:

```bash
make gcp-sim    # docker compose -f docker-compose.gcp-sim.yml up -d
```

---

## Implementation status

All phases P0–P11 complete. See [README.md](README.md) for the full phase table
and [ARCHITECTURE.md](ARCHITECTURE.md) for component details.

| Phase | Scope | Status |
|---|---|---|
| P0 | Infrastructure scaffold | ✅ Done |
| P1 | Ingestion (MinIO, API, DB) | ✅ Done |
| P2 | Classify agent + LLM client | ✅ Done |
| P3 | Schema loader + Extract agent | ✅ Done |
| P4 | Validate agent + Router | ✅ Done |
| P5 | HITL node + Checkpointer + Review UI | ✅ Done |
| P6 | Normalize + universal schema + output writing | ✅ Done |
| P7 | RAG retry (pgvector) + compiled LangGraph + end-to-end wiring | ✅ Done |
| P8 | Query API + semantic synthesizer | ✅ Done |
| P9 | Deterministic verifiers + self-consistency + CI | ✅ Done |
| P10 | Schema versioning + auto-discovery + 4 new doc_types | ✅ Done |
| P11 | Documents dashboard + knowledge graph + HITL queue + phase tracker | ✅ Done |
| P12 | GCP deployment (Cloud Run, GCS, Cloud SQL, Pub/Sub) | 🔲 Planned |
