import logging
import os
import tempfile

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from io_pipeline.ingestion import ingest_file

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


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
    file: UploadFile = File(...), background_tasks: BackgroundTasks = BackgroundTasks()
) -> dict:
    """Accept a document upload, store it, and trigger the pipeline as a background task."""
    # os.path.basename strips directory components — a raw client filename
    # joined into a path enables arbitrary file write (CWE-22).
    safe_name = os.path.basename(file.filename or "upload") or "upload"
    suffix = os.path.splitext(safe_name)[1]

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    # Rename to safe_name so the doc-type regex can parse it
    target_path = os.path.join(os.path.dirname(tmp_path), safe_name)
    os.rename(tmp_path, target_path)

    try:
        document_id = ingest_file(target_path)
    finally:
        try:
            os.unlink(target_path)
        except FileNotFoundError:
            pass

    background_tasks.add_task(_run_pipeline, document_id, safe_name, f"raw/{safe_name}")
    return {"document_id": document_id}
