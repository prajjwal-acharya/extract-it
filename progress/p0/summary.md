# P0 — Scaffold & Contracts

**Commit:** `dc122d8`
**Status:** Complete ✅
**Branch:** `main`

---

## What this phase covers

Structural skeleton only. No agent logic, no LLM calls, no SQL queries, no test assertions.
Goal: working local infra + all data contracts locked so every later phase has a stable interface to build against.

---

## Infrastructure (live, verified)

| Component | Detail | Status |
|---|---|---|
| Postgres | `pgvector/pgvector:pg16` image, `vector 0.8.4` active | ✅ healthy |
| MinIO | `minio/minio:latest`, bucket `documents` | ✅ healthy |
| FastAPI app | `uvicorn` on port 8000, `/health → {"status":"ok"}` | ✅ up |
| Streamlit frontend | port 8501 | ✅ up |
| pgvector extension | enabled via `infra/docker/init.sql` mounted at container init | ✅ confirmed |

**Start everything:** `make up` (alias for `docker compose up -d`)

---

## Files — FULL (complete, working)

### Docker / Compose
- `docker-compose.yml` — 4 services (postgres, minio, app, frontend), healthchecks, `depends_on: condition: service_healthy` for postgres
- `docker-compose.gcp-sim.yml` — PubSub emulator + fake-gcs-server (used from P9)
- `infra/docker/postgres.Dockerfile` — `FROM pgvector/pgvector:pg16`
- `infra/docker/app.Dockerfile` — `python:3.11-slim`, deps installed via `install_deps.py`
- `infra/docker/init.sql` — `CREATE EXTENSION IF NOT EXISTS vector;`
- `infra/docker/install_deps.py` — parses `pyproject.toml` with `tomllib`, installs all deps inside the image without triggering hatchling's editable-install path

### Project config
- `pyproject.toml` — full dep list: `fastapi`, `langgraph`, `langgraph-checkpoint-postgres`, `langsmith`, `google-generativeai`, `psycopg[binary]`, `pgvector`, `minio`, `pypdf`, `streamlit`, `watchdog`, `pyyaml`; dev: `pytest`, `pytest-asyncio`, `httpx`, `vcrpy`, `respx`, `testcontainers[postgresql]`, `ruff`, `mypy`
- `.env.example` — all env vars with local defaults; `GEMINI_MODEL=gemini-2.0-flash`
- `.gitignore`
- `Makefile` — `up / down / test / test-live / migrate / seed / lint / gcp-sim`
- `README.md` — architecture ASCII diagram, phase roadmap table, quick-start

### Config & schemas
- `config/settings.py` — `Pydantic BaseSettings`, `Env` enum (`LOCAL`/`GCP`), all connection strings + API keys, `GEMINI_MODEL = "gemini-2.0-flash"`
- `config/schemas/passport.yaml` — 11 fields: surname, given_names, nationality, dob, sex, place_of_birth, issue/expiry dates, passport_number, mrz_line1/2
- `config/schemas/bank_statement.yaml` — 9 fields: account_holder, account_number, bank_name, period start/end, opening/closing balance, currency, transactions array

### Adapter contracts
- `adapters/object_store/base.py` — `ObjectStore` Protocol: `put / get / list / delete`
- `adapters/trigger/base.py` — `Trigger` Protocol: `on_new_object / start / stop`
- `adapters/factory.py` — `get_object_store()` and `get_trigger()` dispatch on `ENV=LOCAL|GCP`

### Database
- `db/models.py` — 4 SQLAlchemy tables:
  - `Document` — id, filename, doc_type, object_key, status, timestamps, universal_schema (JSON)
  - `ExtractionResult` — per-agent-run record (agent, attempt, raw_output, confidence); supports retries
  - `ConfidenceLog` — per-agent confidence score + reason
  - `DocumentEmbedding` — chunk_text + `Vector(768)` embedding for pgvector similarity search
- `db/session.py` — `engine` + `SessionLocal` + `get_session()`

### Pipeline state contract
- `pipelines/state.py` — `GraphState(TypedDict)` with 16 fields:

  | Field | Type | Reducer |
  |---|---|---|
  | `document_id` | `str` | — |
  | `filename` | `str` | — |
  | `object_key` | `str` | — |
  | `raw_content` | `str` | — |
  | `doc_type` | `str \| None` | — |
  | `classify_confidence` | `float` | — |
  | `extracted_fields` | `Annotated[dict, _keep_last]` | last-write-wins (op_a_retry overwrites each pass) |
  | `extract_confidence` | `float` | — |
  | `validation_issues` | `Annotated[list[str], operator.add]` | accumulates across retry passes |
  | `validate_confidence` | `float` | — |
  | `universal_schema` | `dict` | — |
  | `retry_count` | `int` | — |
  | `hitl_required` | `bool` | — |
  | `hitl_approved` | `bool \| None` | — |
  | `error` | `str \| None` | — |
  | `status` | `str` | — |

  Only `extracted_fields` and `validation_issues` are `Annotated` — these are the two fields rewritten across OP-A retry iterations.

### API
- `api/main.py` — FastAPI app, ingest + query routers mounted, `/health` endpoint

### Observability
- `observability/langsmith_setup.py` — sets `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` env vars; no-op when key is empty

### Alembic
- `infra/migrations/alembic.ini` — `script_location = infra/migrations`
- `infra/migrations/env.py` — full wiring: `target_metadata = Base.metadata`, URL pulled from `settings.DATABASE_URL`
- `infra/migrations/script.py.mako` — standard migration template

---

## Files — STUB (signature + docstring + `raise NotImplementedError`)

| Module | Files |
|---|---|
| `agents/` | `classify_agent.py`, `extract_agent.py`, `validate_agent.py`, `llm_client.py` |
| `agents/base.py` | `AgentResult(BaseModel)` — kept FULL, it is a data schema not logic |
| `pipelines/` | `graph.py`, `router.py` |
| `pipelines/nodes/` | `master.py`, `normalize.py`, `op_a_retry.py`, `op_b_hitl.py` |
| `adapters/object_store/` | `minio_store.py`, `gcs_store.py` |
| `adapters/trigger/` | `local_watch.py`, `pubsub_trigger.py` |
| `io_pipeline/` | `ingestion.py`, `output_writer.py` |
| `query/` | `retriever.py`, `synthesizer.py` |
| `db/` | `checkpointer.py`, `vector_store.py` |
| `api/routes/` | `ingest.py`, `query.py` |
| `api/deps.py` | |
| `frontend/review_app.py` | |
| `observability/tracing.py` | |
| `infra/gcp/` | `cloudrun.yaml`, `deploy.sh`, `eventarc-trigger.yaml` (P9) |
| `scripts/` | `seed_db.py`, `run_local_demo.py` |

---

## Tests — all stubs

| Suite | Files | Tests |
|---|---|---|
| `tests/conftest.py` | 1 | 5 fixture signatures |
| `tests/unit/` | 10 files | ~70 named test functions |
| `tests/integration/` | 6 files (P1↔P2 through P6↔P7) | 18 test functions |
| `tests/e2e/` | 1 file | 4 tests, all `@pytest.mark.live` |

`pytest -m "not live"` result: **76 failed, 4 deselected in 0.09s** — every failure is `NotImplementedError` from a stub body, zero import errors, zero collection errors. Expected and correct.

---

## Key decisions recorded here

| Decision | Rationale |
|---|---|
| `pgvector/pgvector:pg16` not vanilla postgres | pgvector must be compiled into the server; the official pgvector image handles this |
| `gemini-2.0-flash` as default | Confirmed current stable model as of P0 (2026-07-02) |
| `TypedDict` not `Pydantic BaseModel` for state | LangGraph requires TypedDict for its state merging machinery |
| Only `extracted_fields` + `validation_issues` are `Annotated` | These are the only two fields rewritten across OP-A retry iterations; classify/extract write to different keys so no fan-out conflict |
| `google-generativeai` not `google-cloud-aiplatform` | Project uses direct Gemini API (`import google.generativeai`); Vertex AI SDK is a 400 MB install that is never called |
| Separate `ExtractionResult` table | Supports multiple extraction attempts per document (retries) without overwriting |
| Deps installed via `install_deps.py` in Dockerfile | Hatchling's editable install fails on a non-src layout; this extracts deps from `pyproject.toml` via `tomllib` cleanly |

---

## What P0 does NOT do (left for later phases)

- No Alembic migration generated — `alembic upgrade head` has not been run; tables do not yet exist in Postgres
- No LLM calls
- No file ingestion
- No pipeline execution
- No vector embeddings
- No HITL logic
- No GCP deployment
