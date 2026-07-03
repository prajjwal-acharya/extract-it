from fastapi import APIRouter
from pydantic import BaseModel, Field

from agents.llm_client import embed
from query.retriever import retrieve
from query.synthesizer import synthesize

router = APIRouter()


class QueryRequest(BaseModel):
    question: str = Field(min_length=1)


@router.post("/")
def query(req: QueryRequest) -> dict:
    """Embed the question, retrieve relevant chunks, and synthesize a grounded answer."""
    embedding = embed(req.question, task_type="RETRIEVAL_QUERY")
    chunks = retrieve(embedding, top_k=5)
    answer = synthesize(req.question, chunks)
    sources = sorted({c["document_id"] for c in chunks})
    return {"answer": answer, "sources": sources}
