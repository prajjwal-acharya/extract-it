import logging
import os

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from io_pipeline.orchestrator import IngestionOrchestrator
from io_pipeline.validation import ValidationError

router = APIRouter()
logger = logging.getLogger(__name__)


def _run_pipeline(document_id: str, filename: str, object_key: str) -> None:
    try:
        from pipelines.graph import get_graph

        config = {"configurable": {"thread_id": document_id}}
        initial_state = {
            "document_id": document_id,
            "filename": filename,
            "object_key": object_key,
            "retry_count": 0,
            "extracted_fields": {},
            "validation_issues": [],
        }
        get_graph().invoke(initial_state, config=config)  # type: ignore[call-overload]
    except Exception:
        logger.exception("Pipeline failed for document_id=%s", document_id)


@router.post("/")
async def ingest(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> dict:
    """Accept a document upload, store it, and trigger the pipeline."""
    filename = os.path.basename(file.filename or "upload") or "upload"
    data = await file.read()

    def dispatch(doc_id: str, safe_name: str, key: str) -> None:
        background_tasks.add_task(_run_pipeline, doc_id, safe_name, key)

    orchestrator = IngestionOrchestrator(dispatch_fn=dispatch)
    try:
        document_id, is_duplicate = orchestrator.ingest(data, filename, source="http")
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.reason)

    return {"document_id": document_id, "duplicate": is_duplicate}
