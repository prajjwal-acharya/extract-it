from fastapi import APIRouter, HTTPException, Query

from db.models import ConfidenceLog, Document, RetrievalLog
from db.session import get_session

router = APIRouter()


@router.get("/")
def list_documents(
    status: str | None = Query(None),
    doc_type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict]:
    session = get_session()
    q = session.query(Document)
    if status:
        q = q.filter(Document.status == status)
    if doc_type:
        q = q.filter(Document.doc_type == doc_type)
    docs = q.order_by(Document.created_at.desc()).offset(offset).limit(limit).all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "doc_type": d.doc_type,
            "status": d.status,
            "current_phase": d.current_phase,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]


@router.get("/{document_id}")
def get_document(document_id: str) -> dict:
    session = get_session()
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, f"Document {document_id!r} not found")

    confidence_logs = (
        session.query(ConfidenceLog)
        .filter(ConfidenceLog.document_id == document_id)
        .order_by(ConfidenceLog.created_at)
        .all()
    )

    return {
        "id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "current_phase": doc.current_phase,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "universal_schema": doc.universal_schema,
        "extracted_fields": doc.extracted_fields,
        "confidence_logs": [
            {
                "agent": cl.agent,
                "score": cl.score,
                "reason": cl.reason,
                "created_at": cl.created_at.isoformat() if cl.created_at else None,
            }
            for cl in confidence_logs
        ],
    }


@router.get("/{document_id}/references")
def get_document_references(document_id: str) -> list[dict]:
    session = get_session()
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, f"Document {document_id!r} not found")

    logs = (
        session.query(RetrievalLog)
        .filter(RetrievalLog.document_id == document_id)
        .order_by(RetrievalLog.stage, RetrievalLog.similarity_score.desc())
        .all()
    )
    result = []
    for log in logs:
        ref_doc = session.get(Document, log.retrieved_document_id)
        result.append(
            {
                "retrieved_document_id": log.retrieved_document_id,
                "filename": ref_doc.filename if ref_doc else None,
                "doc_type": ref_doc.doc_type if ref_doc else None,
                "stage": log.stage,
                "similarity_score": log.similarity_score,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            }
        )
    return result
