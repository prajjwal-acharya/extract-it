from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from db.models import Document, RetrievalLog

router = APIRouter()


@router.get("/")
def get_knowledge_graph(
    limit: int = Query(50, ge=1, le=500), session: Session = Depends(get_db)
) -> dict:
    docs = session.query(Document).order_by(Document.created_at.desc()).limit(limit).all()
    node_ids = {d.id for d in docs}

    edges_q = session.query(RetrievalLog).filter(
        RetrievalLog.document_id.in_(node_ids),
        RetrievalLog.retrieved_document_id.in_(node_ids),
    )

    return {
        "nodes": [
            {
                "id": d.id,
                "filename": d.filename,
                "doc_type": d.doc_type,
                "status": d.status,
            }
            for d in docs
        ],
        "edges": [
            {
                "source": e.document_id,
                "target": e.retrieved_document_id,
                "stage": e.stage,
                "similarity_score": e.similarity_score,
            }
            for e in edges_q.all()
        ],
    }
