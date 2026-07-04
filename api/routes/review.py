import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import APIKeyHeader
from langgraph.types import Command
from pydantic import BaseModel
from sqlalchemy.orm import Session

from agents.llm_client import embed
from api.deps import get_db
from config.schema_loader import load_schema_model
from config.settings import settings
from db.models import ConfidenceLog, Document, RetrievalLog
from db.vector_store import upsert_embedding

router = APIRouter()

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(key: str | None = Depends(_api_key_header)) -> None:
    if not settings.REVIEW_API_KEY:
        return  # key not configured — open in dev
    if key != settings.REVIEW_API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key")


@router.get("/pending")
def list_pending_review(session: Session = Depends(get_db)) -> list[dict]:
    """Documents currently in awaiting_review phase."""
    docs = (
        session.query(Document)
        .filter(Document.current_phase == "awaiting_review")
        .order_by(Document.created_at.desc())
        .all()
    )
    result = []
    for doc in docs:
        confidence_logs = (
            session.query(ConfidenceLog)
            .filter(ConfidenceLog.document_id == doc.id)
            .order_by(ConfidenceLog.created_at)
            .all()
        )
        references = (
            session.query(RetrievalLog)
            .filter(
                RetrievalLog.document_id == doc.id,
                RetrievalLog.stage == "retry",
            )
            .order_by(RetrievalLog.similarity_score.desc())
            .all()
        )
        ref_list = []
        for log in references:
            ref_doc = session.get(Document, log.retrieved_document_id)
            ref_list.append(
                {
                    "retrieved_document_id": log.retrieved_document_id,
                    "filename": ref_doc.filename if ref_doc else None,
                    "doc_type": ref_doc.doc_type if ref_doc else None,
                    "similarity_score": log.similarity_score,
                }
            )
        result.append(
            {
                "id": doc.id,
                "filename": doc.filename,
                "doc_type": doc.doc_type,
                "status": doc.status,
                "current_phase": doc.current_phase,
                "extracted_fields": doc.extracted_fields,
                "universal_schema": doc.universal_schema,
                "confidence_logs": [
                    {"agent": cl.agent, "score": cl.score, "reason": cl.reason}
                    for cl in confidence_logs
                ],
                "references": ref_list,
            }
        )
    return result


class ReviewDecision(BaseModel):
    approved: bool
    corrections: dict | None = None


@router.post("/{document_id}/decision", dependencies=[Depends(_require_api_key)])
def submit_decision(
    document_id: str, decision: ReviewDecision, session: Session = Depends(get_db)
) -> dict:
    """Resume an interrupted graph run with a human review decision."""
    from pipelines.graph import get_graph

    config = {"configurable": {"thread_id": document_id}}
    graph = get_graph()

    state_snapshot = graph.get_state(config)  # type: ignore[arg-type]
    if state_snapshot is None or not state_snapshot.values:
        raise HTTPException(404, f"No pending review for document_id={document_id!r}")

    doc_type = state_snapshot.values.get("doc_type")
    if decision.corrections and doc_type:
        try:
            model = load_schema_model(doc_type)
            valid_fields = set(model.model_fields) - {"confidence"}
            invalid = set(decision.corrections) - valid_fields
            if invalid:
                raise HTTPException(422, f"Unknown fields for {doc_type!r}: {sorted(invalid)}")
        except FileNotFoundError:
            pass  # unknown doc_type — skip field validation

    result = graph.invoke(Command(resume=decision.model_dump()), config=config)  # type: ignore[call-overload]

    # Path B: embed corrected fields as HITL exemplar for future RAG retrieval.
    if decision.approved and decision.corrections:
        prior_fields = state_snapshot.values.get("extracted_fields") or {}
        merged = {**prior_fields, **decision.corrections}
        chunk_text = json.dumps(merged)
        upsert_embedding(
            session,
            document_id=document_id,
            chunk_index=0,
            chunk_text=chunk_text,
            embedding=embed(chunk_text),
            source="hitl_correction",
        )

    return {"status": "resumed", "state": result}
