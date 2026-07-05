import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from adapters.trigger.local_watch import LocalWatchTrigger
from api.routes.documents import router as documents_router
from api.routes.ingest import router as ingest_router
from api.routes.knowledge_graph import router as knowledge_graph_router
from api.routes.query import router as query_router
from api.routes.review import router as review_router
from api.routes.schema_proposals import router as schema_proposals_router
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


@asynccontextmanager
async def lifespan(app):  # type: ignore[type-arg]
    global _watcher
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


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
