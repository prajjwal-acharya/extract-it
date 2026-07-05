"""Semantic search endpoint — POST /search.

Uses pgvector cosine similarity against stored DocumentEmbedding vectors.
Returns matching documents with similarity score, excerpt, and metadata.
No inference — read-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agents.llm_client import embed
from api.deps import get_db
from db.models import Document
from db.vector_store import similarity_search

router = APIRouter()

_MAX_EXCERPT = 300


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    doc_type: str | None = None
    top_k: int = Field(default=5, ge=1, le=50)


@router.post("/")
def semantic_search(req: SearchRequest, session: Session = Depends(get_db)) -> list[dict]:
    """Embed query and return top-k matching document chunks ranked by cosine similarity."""
    query_embedding = embed(req.query, task_type="RETRIEVAL_QUERY")
    results = similarity_search(session, query_embedding, top_k=req.top_k, doc_type=req.doc_type)

    output = []
    seen_doc_ids: set[str] = set()

    for row, distance in results:
        doc = session.get(Document, row.document_id)
        if doc is None:
            continue

        similarity = round(1.0 - float(distance), 4)
        excerpt = (row.chunk_text or "")[:_MAX_EXCERPT]

        entry: dict = {
            "document_id": row.document_id,
            "filename": doc.filename,
            "doc_type": doc.doc_type,
            "status": doc.status,
            "similarity_score": similarity,
            "excerpt": excerpt,
            "embedding_source": row.source,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        }
        output.append(entry)
        seen_doc_ids.add(row.document_id)

    return output
