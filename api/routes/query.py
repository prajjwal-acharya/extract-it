from fastapi import APIRouter
from pydantic import BaseModel
from query.retriever import retrieve
from query.synthesizer import synthesize

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    top_k: int = 5


class QueryResponse(BaseModel):
    answer: str
    sources: list[str]


@router.post("/", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    placeholder_embedding = [0.0] * 768
    chunks = retrieve(placeholder_embedding, top_k=req.top_k)
    answer = synthesize(req.question, chunks)
    sources = list({c["document_id"] for c in chunks})
    return QueryResponse(answer=answer, sources=sources)
