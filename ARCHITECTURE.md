# Architecture

> This file is a summary overview. The full, detailed architecture reference with
> per-phase breakdowns lives in [architecture/architecture.md](architecture/architecture.md).
> Per-phase docs are in [architecture/](architecture/).

---

## System Overview

Adaptive Document Intelligence Platform — ingests unstructured documents, classifies
them, extracts structured fields via a multi-agent LangGraph pipeline with deterministic
verification, and exposes results through a REST API and Streamlit dashboard.

```
  HTTP upload / file watch / Pub/Sub
              │
              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  io_pipeline  (ingestion)                                  │
  │  SHA-256 dedup → MinIO/GCS → Document row → Postgres       │
  │  triggers pipeline as FastAPI BackgroundTask               │
  └───────────────────────────┬────────────────────────────────┘
                              │
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  LangGraph pipeline  (pipelines/)                          │
  │                                                            │
  │  master → classify ──► extract → truth_engine              │
  │                │               → resolution_planner        │
  │                │               → strategy_executor         │
  │                │                        │                  │
  │                │      ┌─────────────────┼──────────────┐   │
  │                │  ACCEPT           RETRY/...         HITL  │
  │                │  normalize       op_a_retry      op_b_hitl│
  │                │      │               │                │   │
  │                │      │         → truth_engine      normalize│
  │                │      └─────────────────────────────────┘  │
  │                │                     ▼                      │
  │                └──► unknown_handler → persist → END         │
  │                                                            │
  │  _stamp_phase() stamps current_phase before each node      │
  │  Checkpointed in Postgres (langgraph-checkpoint-postgres)  │
  └───────────────────────────┬────────────────────────────────┘
                              │
                              ▼
        ┌────────────────────────────────────────────────┐
        │  PostgreSQL + pgvector                         │
        │  documents  confidence_logs  truth_audit_logs  │
        │  persistence_audit_logs  document_embeddings   │
        │  retrieval_logs  schema_versions               │
        │  schema_proposal_records  LangGraph checkpoints│
        └──────────────┬─────────────────────────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
    ┌──────────────────┐   ┌──────────────────┐
    │  MinIO / GCS     │   │  LangSmith       │
    │  object store    │   │  (traces)        │
    └──────────────────┘   └──────────────────┘
              │
              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  FastAPI + 7-page Streamlit Dashboard                      │
  │  Upload │ Documents │ Search │ Review Queue                │
  │  Schema Proposals │ Analytics │ Knowledge Map              │
  └────────────────────────────────────────────────────────────┘
```

---

## Graph Topology (actual)

From `pipelines/graph.py`:

```
master → classify ──[_route_after_classify]──► extract         (PROCEED)
                                             ► unknown_handler  (UNKNOWN | FAILURE)
unknown_handler → persist → END

extract → truth_engine → resolution_planner → strategy_executor
strategy_executor ──[route_after_executor]──► normalize         (ACCEPT)
                                            ► op_a_retry        (RETRY / PROMPT_REFINEMENT /
                                                                  BETTER_RETRIEVAL /
                                                                  IMAGE_PREPROCESS /
                                                                  MODEL_ESCALATION)
                                            ► op_b_hitl         (HITL)
                                            ► persist           (REJECT)

op_a_retry → truth_engine    (retry loop back through full resolution cycle)

op_b_hitl ──[route_after_hitl]──► normalize   (always — both approve and reject)
normalize → persist → END
```

---

## Repository Layout

```
extract-it/
├── api/                routes/ (ingest, documents, review, search, analytics, ...)
├── agents/             classify_agent, extract_agent, schema_diff_agent, verifiers, llm_client
├── pipelines/
│   ├── graph.py        build_graph(), _stamp_phase, lazy singleton
│   ├── state.py        GraphState TypedDict with Annotated reducers
│   ├── registry.py     DocumentRegistry — all 8 supported doc types + UNKNOWN
│   ├── router.py       route_after_executor / route_after_hitl
│   ├── nodes/          classify, extract, truth_engine, resolution_planner,
│   │                   strategy_executor, normalize, op_a_retry, op_b_hitl, ...
│   ├── truth_engine/   TruthReport, VerifierRegistry, verifier_registry.py
│   ├── resolution/     ResolutionDecision, Strategy, planner, executor, directives
│   └── learning/       LearningPolicy, reviewer_payload, schema_proposal
├── db/                 models, session, vector_store, checkpointer
├── io_pipeline/        ingestion, orchestrator, hashing, output_writer (4-phase)
├── config/             settings, schema_loader, schemas/*.yaml
├── adapters/           MinioStore|GCSStore, LocalWatchTrigger|PubSubTrigger
├── query/              retriever, synthesizer
├── frontend/           7-page Streamlit app + api_client + smoke tests
├── observability/      LangSmith setup + tracing
├── infra/              docker, migrations (Alembic), gcp
├── tests/              unit, integration, e2e
├── architecture/       ← full architecture docs (this folder)
└── scripts/            manual smoke helpers
```

---

## Key Design Decisions

- **LangGraph TypedDict state** with `Annotated` reducers (`_keep_last`, `operator.add`)
  for fields written by multiple nodes
- **Truth Engine** as the sole post-extraction evidence authority — replaces static
  validate_agent threshold routing with TruthReport + ResolutionDecision
- **Resolution Engine** externalises the "what to do next" decision into a pluggable
  strategy system (8 strategies) with a full audit trail per run
- **route_after_hitl always returns normalize** — universal_schema computed for all
  documents regardless of HITL outcome; persist reads hitl_approved for terminal status
- **Atomic 4-phase persist** — any failure after Phase A → persist_failed status;
  document never shows completed unless all writes succeeded
- **LearningPolicy as sole embedding authority** — single code path for embeddings
- **DB-first schema loading with YAML as bootstrap** — atomic version bumps, partial
  unique index, clean audit trail; YAML seeded once by Alembic
- **Running balance for balance arithmetic** — uses last transaction's running balance
  column instead of summing debit/credit to avoid double-counting opening-balance rows
- **Startup recovery** — lifespan scans for stranded documents on every app start,
  re-queues them in daemon threads with 30 s table-existence wait

---

## Full Documentation Index

| Document | What it covers |
|---|---|
| [architecture/architecture.md](architecture/architecture.md) | Complete system reference |
| [architecture/phase-p0-scaffold.md](architecture/phase-p0-scaffold.md) | P0: skeleton, contracts, GraphState |
| [architecture/phase-p1-ingestion.md](architecture/phase-p1-ingestion.md) | P1: ingest, dedup, object store |
| [architecture/phase-p2-classification.md](architecture/phase-p2-classification.md) | P2: classify_agent, registry, routing |
| [architecture/phase-p3-extraction.md](architecture/phase-p3-extraction.md) | P3: extract_agent, schema loader, self-consistency |
| [architecture/phase-p4-truth-engine.md](architecture/phase-p4-truth-engine.md) | P4: Truth Engine, VerifierRegistry, TruthReport |
| [architecture/phase-p5-resolution-engine.md](architecture/phase-p5-resolution-engine.md) | P5: Resolution Engine, strategies, executor |
| [architecture/phase-p5-hitl.md](architecture/phase-p5-hitl.md) | P5-HITL: interrupt/resume, review API, LearningPolicy |
| [architecture/phase-p6-rag-retry.md](architecture/phase-p6-rag-retry.md) | P6: pgvector, op_a_retry, schema_diff, retrieval logging |
| [architecture/phase-p7-query.md](architecture/phase-p7-query.md) | P7: semantic Q&A, synthesizer, /query |
| [architecture/phase-p8-verifiers.md](architecture/phase-p8-verifiers.md) | P8: all verifier implementations, CI pipeline |
| [architecture/phase-p9-schema-versioning.md](architecture/phase-p9-schema-versioning.md) | P9: schema_versions, auto-discovery, new doc types |
| [architecture/phase-p10-normalization.md](architecture/phase-p10-normalization.md) | P10: normalize_node, universal_schema, fallback |
| [architecture/phase-p11-persistence.md](architecture/phase-p11-persistence.md) | P11: atomic persist, audit logs, persist_failed |
| [architecture/phase-p12-explainability.md](architecture/phase-p12-explainability.md) | P12: search, timeline, explain, analytics |
| [architecture/phase-p13-dashboard.md](architecture/phase-p13-dashboard.md) | P13: 7-page Streamlit UI, api_client, smoke tests |
| [architecture/phase-p14-gcp.md](architecture/phase-p14-gcp.md) | P14: GCP deployment plan (planned) |
