from fastapi import APIRouter

router = APIRouter()


@router.post("/")
async def ingest(file) -> dict:
    """Accept a document upload, store it, and enqueue the pipeline as a background task."""
    raise NotImplementedError
