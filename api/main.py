import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from adapters.trigger.local_watch import LocalWatchTrigger
from api.routes.analytics import router as analytics_router
from api.routes.documents import router as documents_router
from api.routes.ingest import router as ingest_router
from api.routes.knowledge_graph import router as knowledge_graph_router
from api.routes.query import router as query_router
from api.routes.review import router as review_router
from api.routes.schema_proposals import router as schema_proposals_router
from api.routes.search import router as search_router
from config.settings import settings
from io_pipeline.orchestrator import IngestionOrchestrator
from observability.langsmith_setup import setup_langsmith

setup_langsmith()

WATCH_DIR = os.environ.get("WATCH_DIR", "/tmp/extract_it_watch")


def _folder_dispatch(doc_id: str, safe_name: str, key: str) -> None:
    """Synchronous dispatch for folder-watcher trigger."""
    from pipelines.graph import get_graph as _gg

    config = {"configurable": {"thread_id": doc_id}}
    initial_state = {
        "document_id": doc_id,
        "filename": safe_name,
        "object_key": key,
        "retry_count": 0,
        "extracted_fields": {},
        "validation_issues": [],
    }
    try:
        _gg().invoke(initial_state, config=config)  # type: ignore[call-overload]
    except Exception:
        logging.getLogger(__name__).exception("Folder-watch pipeline failed for %s", doc_id)


_watcher: LocalWatchTrigger | None = None


_log = logging.getLogger(__name__)


def _recover_stranded_documents() -> None:
    """Re-run pipeline for documents killed mid-flight by a container restart.

    A document is stranded when its phase is an in-progress value (not a
    terminal one) and write_output hasn't run (status still 'queued').
    """
    import threading

    from db.models import Document
    from db.session import session_scope
    from pipelines.graph import get_graph

    _IN_PROGRESS_PHASES = {
        "ingested",
        "classifying",
        "extracting",
        "evaluating",
        "planning",
        "executing",
        "retrying",
        "normalizing",
        "finalizing",
    }

    # Wait up to 30s for migrations to have created the documents table.
    import time

    for _ in range(6):
        try:
            from sqlalchemy import text

            with session_scope() as session:
                session.execute(text("SELECT 1 FROM documents LIMIT 1"))
            break
        except Exception:
            time.sleep(5)
    else:
        _log.warning("startup recovery: documents table not ready after 30s, skipping")
        return

    try:
        with session_scope() as session:
            stranded = (
                session.query(Document)
                .filter(
                    Document.status == "queued",
                    Document.current_phase.in_(_IN_PROGRESS_PHASES),
                )
                .all()
            )
            rows = [(d.id, d.filename, d.object_key) for d in stranded]
    except Exception:
        _log.warning("startup recovery: DB query failed", exc_info=True)
        return

    if not rows:
        return

    _log.info("startup recovery: re-queuing %d stranded document(s)", len(rows))
    graph = get_graph()

    def _rerun(doc_id: str, filename: str, object_key: str) -> None:
        try:
            config = {"configurable": {"thread_id": doc_id}}
            initial_state = {
                "document_id": doc_id,
                "filename": filename,
                "object_key": object_key,
                "retry_count": 0,
                "extracted_fields": {},
                "validation_issues": [],
            }
            graph.invoke(initial_state, config=config)  # type: ignore[call-overload]
            _log.info("startup recovery: completed document_id=%s", doc_id)
        except Exception:
            _log.exception("startup recovery: pipeline failed for document_id=%s", doc_id)

    for doc_id, filename, object_key in rows:
        # daemon=False so uvicorn graceful shutdown waits; but we set a short
        # enough timeout that watchfiles reloads don't block indefinitely.
        t = threading.Thread(target=_rerun, args=(doc_id, filename, object_key), daemon=False)
        t.start()


@asynccontextmanager
async def lifespan(app):  # type: ignore[type-arg]
    global _watcher
    # Warm up the checkpointer (runs setup()/CREATE INDEX) once at startup,
    # before recovery threads start — prevents concurrent setup() deadlocks
    # when multiple hot-reload processes each try to CREATE INDEX CONCURRENTLY.
    from db.checkpointer import get_checkpointer

    get_checkpointer()
    _recover_stranded_documents()
    try:
        _orch = IngestionOrchestrator(dispatch_fn=_folder_dispatch)
        _watcher = LocalWatchTrigger(WATCH_DIR, settle_secs=settings.WATCH_SETTLE_SECS)

        def _read_and_ingest(path: str) -> None:
            with open(path, "rb") as fh:
                data = fh.read()
            _orch.ingest(data, path, source="folder_watch")

        _watcher.on_new_object(_read_and_ingest)
        _watcher.start()
    except Exception:
        logging.getLogger(__name__).warning("Folder watcher not started", exc_info=True)
    yield
    if _watcher:
        _watcher.stop()


app = FastAPI(title="Doc Intel Platform", version="0.1.0", lifespan=lifespan)
app.include_router(ingest_router, prefix="/ingest", tags=["ingest"])
app.include_router(query_router, prefix="/query", tags=["query"])
app.include_router(review_router, prefix="/review", tags=["review"])
app.include_router(documents_router, prefix="/documents", tags=["documents"])
app.include_router(knowledge_graph_router, prefix="/knowledge-graph", tags=["knowledge-graph"])
app.include_router(schema_proposals_router, prefix="/schema-proposals", tags=["schema-proposals"])
app.include_router(search_router, prefix="/search", tags=["search"])
app.include_router(analytics_router, prefix="/analytics", tags=["analytics"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
