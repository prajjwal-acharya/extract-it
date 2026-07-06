# P0 — Scaffold

**Status:** ✅ Done  
**Scope:** Project skeleton, contracts, TypedDict state, base infra

---

## What P0 delivered

P0 is the structural foundation on which every subsequent phase builds. Nothing runs
against a real database or LLM in this phase — it establishes contracts, directory
layout, and the shared state model that all pipeline nodes write to.

---

## Directory skeleton

```
extract-it/
├── api/                    FastAPI app factory (no routes yet)
├── agents/                 Agent contracts (AgentResult dataclass)
├── pipelines/
│   └── state.py            GraphState TypedDict — sole shared state container
├── db/
│   ├── models.py           Document + ConfidenceLog ORM models
│   └── session.py          Engine + SessionLocal factory
├── io_pipeline/
│   └── ingestion.py        ingest_file() stub
├── config/
│   ├── settings.py         Pydantic Settings with env-file support
│   └── schemas/            YAML schema stubs
├── infra/
│   ├── docker/             Dockerfiles, init.sql (pgvector)
│   └── migrations/         Alembic env + first migration
├── docker-compose.yml      postgres + minio + app + frontend
├── Makefile                up / down / migrate / test / lint
└── pyproject.toml          dependencies, ruff, mypy, pytest config
```

---

## GraphState (the shared contract)

`pipelines/state.py` defines `GraphState` as a LangGraph `TypedDict`. Every pipeline
node reads from and writes to this single dict. Fields that multiple nodes write use
`Annotated` reducers so LangGraph knows how to merge concurrent updates:

| Field | Type | Reducer | Purpose |
|---|---|---|---|
| `document_id` | `str` | last-write | document UUID |
| `filename` | `str` | last-write | original filename |
| `object_key` | `str` | last-write | MinIO/GCS path |
| `raw_bytes` | `bytes` | last-write | raw file content |
| `doc_type` | `str \| None` | last-write | classified type |
| `extracted_fields` | `dict` | `_keep_last` | op_a_retry overwrites each pass |
| `validation_issues` | `list[str]` | `operator.add` | accumulated across passes |
| `execution_history` | `list[ExecutionRecord]` | `operator.add` | per-pass records |
| `tool_call_count` | `int` | `operator.add` | verifier budget |
| `universal_schema` | `dict` | last-write | canonical 3-field output |
| `hitl_required` | `bool` | last-write | escalation flag |
| `hitl_approved` | `bool \| None` | last-write | human decision |
| `error` | `str \| None` | last-write | error message |
| `status` | `str` | last-write | pipeline status |

`_keep_last` is a custom reducer: returns the update if it is not `None`, else keeps
the current value. This allows `op_a_retry` to overwrite `extracted_fields` without
LangGraph raising an update-conflict error.

---

## AgentResult contract

`agents/base.py` defines the return type for every agent function:

```python
@dataclass
class AgentResult:
    success: bool
    confidence: float           # 0.0 – 1.0
    data: dict                  # agent-specific output
    reason: str | None          # human-readable explanation
    tool_calls_made: int        # verifier tool calls consumed
    verification_passed: bool | None  # deterministic verifier result
```

All agents must return an `AgentResult`. Graph nodes translate `AgentResult.data`
into specific `GraphState` field updates.

---

## Pydantic Settings

`config/settings.py` uses `pydantic-settings` with `env_file=".env"`. All
configuration is typed and validated at startup. Key fields established in P0:

```python
class Settings(BaseSettings):
    ENV: str = "LOCAL"
    DATABASE_URL: str
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_BUCKET: str = "documents"
    GOOGLE_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    CONFIDENCE_THRESHOLD: float = 0.85
    MAX_RETRIES: int = 2
    EMBEDDING_DIMENSIONS: int = 768
```

---

## Docker Compose stack

`docker-compose.yml` defines the four services that the local dev stack runs:

| Service | Port | Image |
|---|---|---|
| `postgres` | 5432 | Custom (postgres.Dockerfile, adds pgvector) |
| `minio` | 9000, 9001 | `minio/minio:latest` |
| `app` | 8000 | Custom (app.Dockerfile, Python 3.11) |
| `frontend` | 8501 | Same image as app, different command |

`postgres` and `minio` have healthchecks; `app` depends on both being healthy.
Both `app` and `frontend` mount the repo root at `/app` and run with `--reload`
(uvicorn) or directly (streamlit), enabling live code updates without a full rebuild.

**Operational note:** uvicorn `--reload` (watchfiles) kills background threads on any
`.py` file edit. This is a known constraint — do not edit source files while a pipeline
is running in the app container.

---

## First Alembic migration

`infra/migrations/versions/da5070439f01_create_core_tables.py` creates:
- `documents` — core document state table
- `confidence_logs` — per-agent confidence scores

The `pgvector` extension is initialised by `infra/docker/init.sql` (runs once at
container first start via `docker-entrypoint-initdb.d`).

---

## Makefile targets established in P0

| Target | Command |
|---|---|
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down -v` |
| `make migrate` | Alembic upgrade head (inside container) |
| `make test` | `pytest tests/ -m "not live"` |
| `make lint` | `ruff check . && mypy .` |
| `make format` | `ruff format .` |

---

## What P0 does NOT include

- No LLM calls
- No routing or pipeline execution
- No HITL
- No RAG
- No API routes (only the app factory)
- No schema versioning
