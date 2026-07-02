import os
import tempfile

from fastapi import APIRouter, File, UploadFile

from io_pipeline.ingestion import ingest_file

router = APIRouter()


@router.post("/")
async def ingest(file: UploadFile = File(...)) -> dict:
    """Accept a document upload, store it, and create a Document DB row."""
    suffix = os.path.splitext(file.filename or "upload")[1]
    original_name = file.filename or "upload"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    # Rename temp file to match original filename so the regex can parse it
    target_path = os.path.join(os.path.dirname(tmp_path), original_name)
    os.rename(tmp_path, target_path)

    try:
        document_id = ingest_file(target_path)
    finally:
        try:
            os.unlink(target_path)
        except FileNotFoundError:
            pass

    return {"document_id": document_id}
