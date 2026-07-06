# Starting the Project

## Prerequisites

- Docker Desktop running
- Python 3.11+ (via pyenv or system)
- `pip install -e ".[dev]"` — run once for local tooling (pytest, ruff, mypy)

---

## First-time setup

### 1. Copy environment file

```bash
cp .env.example .env
```

Edit `.env` and fill in:

| Key | Required for | Notes |
|---|---|---|
| `GOOGLE_API_KEY` | Live LLM calls (classify, extract, embed) | Leave empty to run mocked tests only |
| `LANGCHAIN_API_KEY` | LangSmith tracing | Optional — app works without it |
| `REVIEW_API_KEY` | HITL + schema-proposal auth | Optional — if unset, those routes are open (dev mode) |
| All others | Local infra | Defaults in `.env.example` work as-is |

### 2. Build and start all services

```bash
make up
```

Runs `docker compose up -d --build`. Builds the `app` and `frontend` images and
starts postgres, minio, app, and frontend. Wait ~10 s for healthchecks, then verify:

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

### 3. Run database migrations

```bash
make migrate
```

Applies all Alembic migrations in order. Safe to re-run — idempotent.

Tables created on first run:
- `documents` — core document state
- `confidence_logs` — per-agent confidence scores
- `document_embeddings` — 768-dim pgvector embeddings
- `schema_versions` — versioned doc-type schemas
- `retrieval_logs` — RAG retrieval edges
- `truth_audit_logs` — Truth Engine verification results
- `persistence_audit_logs` — per-run persist decisions
- `schema_proposal_records` — human-gated schema changes

Also seeds all doc-type schemas from `config/schemas/*.yaml` (passport, bank_statement,
driving_license, aadhaar, gst_invoice, salary_slip, itr, property_deed).

### 4. Initialise LangGraph checkpointer tables

These are outside Alembic and must be run once per fresh database:

```bash
make checkpointer
```

### 5. Verify

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

Open the dashboard: **http://localhost:8501**

---

## Day-to-day start

```bash
make up        # start (or restart) all services; rebuilds only if Dockerfile changed
make migrate   # safe to re-run; no-op when already up to date
```

No need to re-run `make checkpointer` unless you wiped volumes with `make down`.

---

## Stopping

```bash
docker compose down        # stops containers, keeps volumes (data survives)
make down                  # stops containers AND deletes volumes (clean slate)
```

---

## Running tests

```bash
# Unit + integration, no live API calls (fast — all I/O mocked)
make test

# All tests including live LLM calls (requires GOOGLE_API_KEY)
make test-live

# Dashboard smoke tests (headless AppTest + ApiClient unit tests)
make test-smoke
```

Unit tests run without Docker. Integration tests use testcontainers to spin up a
real Postgres — requires Docker Desktop.

---

## Running the dashboard locally (outside Docker)

Useful when iterating on frontend code without a full Docker rebuild:

```bash
make dashboard
# equivalent: API_BASE_URL=http://localhost:8000 streamlit run frontend/app.py
```

The dashboard reads `API_BASE_URL` from the environment to know where the API is.
In Docker it is set to `http://app:8000` automatically.

---

## Smoke tests (manual pipeline verification)

Get a `document_id` first by posting a file to `POST /ingest/`.

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
| Dashboard | http://localhost:8501 | 7-page Streamlit — Upload, Documents, Search, Review Queue, Schema Proposals, Analytics, Knowledge Map |
| MinIO console | http://localhost:9001 | Login: `minioadmin` / `minioadmin` |
| Postgres | `localhost:5432` | DB: `docint`, user: `user`, pass: `password` |

> **Port conflict**: if you have a local Postgres on 5432, `make migrate` and
> `make checkpointer` are unaffected — they run inside the container.
> For direct `psql` access from the host:
> `docker compose exec postgres psql -U user -d docint`

---

## Clearing data (dev reset)

`clear_data.sh` wipes all document data and re-runs migrations:

```bash
bash clear_data.sh
```

> **Note**: Run `make checkpointer` again after `clear_data.sh` — it drops and
> recreates the LangGraph checkpoint tables which Alembic does not manage.

---

## GCP simulation (local)

To test the GCP adapters (GCS object store, Pub/Sub trigger) without a live GCP project:

```bash
make gcp-sim
```

---

## Make targets reference

| Target | What it does |
|---|---|
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down -v` (deletes volumes) |
| `make logs` | `docker compose logs -f` |
| `make migrate` | Alembic upgrade head (inside container) |
| `make checkpointer` | Initialise LangGraph checkpoint tables (once per fresh DB) |
| `make seed` | Run `scripts/seed_db.py` |
| `make test` | `pytest tests/ -m "not live"` |
| `make test-live` | `pytest tests/` (includes live LLM calls) |
| `make test-smoke` | `pytest frontend/tests/` (dashboard smoke tests) |
| `make lint` | `ruff check . && mypy .` |
| `make format` | `ruff format .` |
| `make dashboard` | Run dashboard locally against `http://localhost:8000` |
| `make gcp-sim` | Start GCP emulators (Pub/Sub + GCS) |

---

## Operational constraints

**Watchfiles / uvicorn --reload**: the `app` container uses `uvicorn --reload`
(watchfiles). Any `.py` file edit triggers a server reload, which kills all
background pipeline threads. Do not edit source files while a pipeline is running.

**Startup recovery**: on every app start, `_recover_stranded_documents()` in
`api/main.py` scans for documents with `status=queued` AND `current_phase` in an
in-progress set, and re-queues them automatically. This recovers documents that were
mid-flight when the container was restarted.

---

## Implementation status

| Phase | Scope | Status |
|---|---|---|
| P0 | Infrastructure scaffold | ✅ Done |
| P1 | Ingestion (MinIO, API, DB, dedup) | ✅ Done |
| P2 | Classify agent + routing engine + DocumentRegistry | ✅ Done |
| P3 | Schema loader + Extract agent + self-consistency | ✅ Done |
| P4 | Truth Engine (deterministic verifiers, TruthReport) | ✅ Done |
| P5 | Resolution Engine (ResolutionPlanner, StrategyExecutor) | ✅ Done |
| P5-HITL | Human-in-the-loop: op_b_hitl, checkpointer, LearningPolicy | ✅ Done |
| P6 | RAG retry (pgvector) + schema_diff_agent + retrieval logging | ✅ Done |
| P7 | Query API + semantic synthesizer | ✅ Done |
| P8 | Deterministic verifiers + CI pipeline | ✅ Done |
| P9 | Schema versioning + auto-discovery + 4 new doc_types | ✅ Done |
| P10 | Normalize + universal schema + fallback mapping | ✅ Done |
| P11 | Transactional persistence + audit logs + schema proposals API | ✅ Done |
| P12 | Query & Explainability API (search, similar, timeline, explain, analytics) | ✅ Done |
| P13 | 7-page Streamlit dashboard (api_client, dark theme, smoke tests) | ✅ Done |
| P14 | GCP deployment (Cloud Run, GCS, Cloud SQL, Pub/Sub) | 🔲 Planned |

See [README.md](README.md) for API endpoints and [architecture/architecture.md](architecture/architecture.md) for component details.
