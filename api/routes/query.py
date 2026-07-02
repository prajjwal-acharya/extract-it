from fastapi import APIRouter

router = APIRouter()


@router.post("/")
def query(req) -> dict:
    """Embed the question, retrieve relevant chunks, and synthesize a grounded answer."""
    raise NotImplementedError
