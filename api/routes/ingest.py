import os
import tempfile

from fastapi import APIRouter, File, HTTPException, UploadFile

from io_pipeline.ingestion import ingest_file

router = APIRouter()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


@router.post("/")
async def ingest(file: UploadFile = File(...)) -> dict:
    """Accept a document upload, store it, and create a Document DB row."""
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

    return {"document_id": document_id}
