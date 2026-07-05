"""Document API — list, canonical explorer, similar, timeline, explain.

All endpoints are read-only.  No inference, no planner changes.
Artifacts are read directly from the DB tables written by previous phases.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from db.models import (
    ConfidenceLog,
    Document,
    DocumentEmbedding,
    PersistenceAuditLog,
    RetrievalLog,
    TruthAuditLog,
)
from db.vector_store import similarity_search

router = APIRouter()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _doc_or_404(document_id: str, session: Session) -> Document:
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, f"Document {document_id!r} not found")
    return doc


def _confidence_logs(document_id: str, session: Session) -> list[ConfidenceLog]:
    return (
        session.query(ConfidenceLog)
        .filter(ConfidenceLog.document_id == document_id)
        .order_by(ConfidenceLog.created_at)
        .all()
    )


def _truth_audit(document_id: str, session: Session) -> TruthAuditLog | None:
    return (
        session.query(TruthAuditLog)
        .filter(TruthAuditLog.document_id == document_id)
        .order_by(TruthAuditLog.created_at.desc())
        .first()
    )


def _persistence_audit(document_id: str, session: Session) -> PersistenceAuditLog | None:
    return (
        session.query(PersistenceAuditLog)
        .filter(PersistenceAuditLog.document_id == document_id)
        .order_by(PersistenceAuditLog.created_at.desc())
        .first()
    )


def _retrieval_logs(document_id: str, session: Session) -> list[RetrievalLog]:
    return (
        session.query(RetrievalLog)
        .filter(RetrievalLog.document_id == document_id)
        .order_by(RetrievalLog.created_at)
        .all()
    )


def _fmt_ts(dt) -> str | None:  # type: ignore[type-arg]
    return dt.isoformat() if dt else None


# ---------------------------------------------------------------------------
# GET / — document list
# ---------------------------------------------------------------------------


@router.get("/")
def list_documents(
    status: str | None = Query(None),
    doc_type: str | None = Query(None),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db),
) -> list[dict]:
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
            "created_at": _fmt_ts(d.created_at),
        }
        for d in docs
    ]


# ---------------------------------------------------------------------------
# GET /{id} — canonical document explorer
# ---------------------------------------------------------------------------


@router.get("/{document_id}")
def get_document(document_id: str, session: Session = Depends(get_db)) -> dict:
    """Return all persisted artifacts for a document in a single response.

    Aggregates: metadata, extracted fields, TruthReport, ResolutionDecision,
    LearningDecision, PersistenceAudit, ConfidenceLogs, and retrieval history.
    """
    doc = _doc_or_404(document_id, session)
    logs = _confidence_logs(document_id, session)
    truth = _truth_audit(document_id, session)
    persist = _persistence_audit(document_id, session)
    retrieval = _retrieval_logs(document_id, session)

    truth_report = None
    if truth is not None:
        truth_report = {
            "final_confidence": truth.final_confidence,
            "decision_reason": truth.decision_reason,
            "coverage_score": truth.coverage_score,
            "required_fields_missing": truth.required_fields_missing,
            "additional_fields": truth.additional_fields,
            "verification_reports": truth.verification_reports,
            "document_status": truth.document_status,
            "allow_completion": truth.allow_completion,
            "allow_embedding": truth.allow_embedding,
            "allow_learning": truth.allow_learning,
            "persistence_reason": truth.persistence_reason,
            "verifier_version": truth.verifier_version,
        }

    resolution = None
    learning = None
    persistence_audit = None
    if persist is not None:
        resolution = {
            "strategy": persist.resolution_strategy,
            "reason": persist.resolution_reason,
            "requires_human": persist.resolution_requires_human,
            "learning_candidate": persist.learning_candidate,
        }
        learning = {
            "allow_learning": persist.allow_learning,
            "learn_from_document": persist.learn_from_document,
            "learn_from_correction": persist.learn_from_correction,
            "schema_candidate": persist.schema_candidate,
            "reason": persist.learning_reason,
            "schema_proposal": persist.schema_proposal_json,
        }
        persistence_audit = {
            "persist_status": persist.persist_status,
            "persist_reason": persist.persist_reason,
            "created_at": _fmt_ts(persist.created_at),
        }

    retrieval_history = []
    for rl in retrieval:
        ref_doc = session.get(Document, rl.retrieved_document_id)
        retrieval_history.append(
            {
                "retrieved_document_id": rl.retrieved_document_id,
                "filename": ref_doc.filename if ref_doc else None,
                "doc_type": ref_doc.doc_type if ref_doc else None,
                "stage": rl.stage,
                "similarity_score": rl.similarity_score,
                "created_at": _fmt_ts(rl.created_at),
            }
        )

    return {
        "id": doc.id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "current_phase": doc.current_phase,
        "created_at": _fmt_ts(doc.created_at),
        "updated_at": _fmt_ts(doc.updated_at),
        "extracted_fields": doc.extracted_fields,
        "universal_schema": doc.universal_schema,
        "truth_report": truth_report,
        "resolution": resolution,
        "learning": learning,
        "persistence_audit": persistence_audit,
        "confidence_logs": [
            {
                "agent": cl.agent,
                "score": cl.score,
                "reason": cl.reason,
                "created_at": _fmt_ts(cl.created_at),
            }
            for cl in logs
        ],
        "retrieval_history": retrieval_history,
    }


# ---------------------------------------------------------------------------
# GET /{id}/references — kept for backwards compatibility
# ---------------------------------------------------------------------------


@router.get("/{document_id}/references")
def get_document_references(document_id: str, session: Session = Depends(get_db)) -> list[dict]:
    _doc_or_404(document_id, session)
    logs = _retrieval_logs(document_id, session)
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
                "created_at": _fmt_ts(log.created_at),
            }
        )
    return result


# ---------------------------------------------------------------------------
# GET /{id}/similar — similar documents by embedding
# ---------------------------------------------------------------------------


@router.get("/{document_id}/similar")
def get_similar_documents(
    document_id: str,
    top_k: int = Query(default=5, ge=1, le=50),
    session: Session = Depends(get_db),
) -> list[dict]:
    """Return top-k documents most similar to this document by pgvector cosine distance."""
    _doc_or_404(document_id, session)

    emb_row = (
        session.query(DocumentEmbedding).filter_by(document_id=document_id, chunk_index=0).first()
    )
    if emb_row is None or emb_row.embedding is None:
        return []

    # Retrieve top_k+1 so we can drop the document itself from results
    results = similarity_search(session, list(emb_row.embedding), top_k=top_k + 1)
    output = []
    for row, distance in results:
        if row.document_id == document_id:
            continue
        ref_doc = session.get(Document, row.document_id)
        if ref_doc is None:
            continue
        output.append(
            {
                "document_id": row.document_id,
                "filename": ref_doc.filename,
                "doc_type": ref_doc.doc_type,
                "status": ref_doc.status,
                "similarity_score": round(1.0 - float(distance), 4),
                "embedding_source": row.source,
                "created_at": _fmt_ts(ref_doc.created_at),
            }
        )
        if len(output) >= top_k:
            break

    return output


# ---------------------------------------------------------------------------
# GET /{id}/timeline — ordered execution events
# ---------------------------------------------------------------------------

_AGENT_TO_EVENT = {
    "classify": "classification",
    "extract": "extraction",
    "truth_engine": "truth_engine",
    "schema_diff": "schema_validation",
    "persist": "persistence",
}


def _build_timeline(
    doc: Document,
    logs: list[ConfidenceLog],
    retrieval: list[RetrievalLog],
    truth: TruthAuditLog | None,
    persist: PersistenceAuditLog | None,
) -> list[dict]:
    events: list[dict] = []

    events.append(
        {
            "event": "upload",
            "timestamp": _fmt_ts(doc.created_at),
            "confidence": None,
            "reason": None,
            "strategy": None,
            "model": None,
            "duration_ms": None,
        }
    )

    # Track per-agent occurrence index to label retries
    agent_counts: dict[str, int] = {}
    prev_ts = doc.created_at

    for cl in logs:
        agent_counts[cl.agent] = agent_counts.get(cl.agent, 0) + 1
        count = agent_counts[cl.agent]
        event_name = _AGENT_TO_EVENT.get(cl.agent, cl.agent)
        if count > 1:
            event_name = f"{event_name}_retry_{count - 1}"

        # Supplement persist event with resolution strategy label
        strategy = None
        if cl.agent == "persist" and persist is not None:
            strategy = persist.resolution_strategy

        duration_ms = None
        if prev_ts and cl.created_at:
            delta = (cl.created_at - prev_ts).total_seconds()
            duration_ms = round(delta * 1000)
        prev_ts = cl.created_at

        events.append(
            {
                "event": event_name,
                "timestamp": _fmt_ts(cl.created_at),
                "confidence": cl.score,
                "reason": cl.reason,
                "strategy": strategy,
                "model": None,
                "duration_ms": duration_ms,
            }
        )

    # Inject HITL event if document went through review
    if persist is not None and persist.resolution_requires_human:
        hitl_event = {
            "event": "human_review",
            "timestamp": _fmt_ts(persist.created_at),
            "confidence": None,
            "reason": persist.resolution_reason,
            "strategy": "hitl",
            "model": None,
            "duration_ms": None,
        }
        # Insert before the persist event (last entry)
        if events and events[-1]["event"] == "persistence":
            events.insert(-1, hitl_event)
        else:
            events.append(hitl_event)

    # Inject retrieval events
    for rl in retrieval:
        events.append(
            {
                "event": f"retrieval:{rl.stage}",
                "timestamp": _fmt_ts(rl.created_at),
                "confidence": rl.similarity_score,
                "reason": f"retrieved {rl.retrieved_document_id}",
                "strategy": None,
                "model": None,
                "duration_ms": None,
            }
        )

    events.sort(key=lambda e: e["timestamp"] or "")
    return events


@router.get("/{document_id}/timeline")
def get_document_timeline(document_id: str, session: Session = Depends(get_db)) -> list[dict]:
    """Return ordered execution events reconstructed from persisted audit artifacts."""
    doc = _doc_or_404(document_id, session)
    logs = _confidence_logs(document_id, session)
    retrieval = _retrieval_logs(document_id, session)
    truth = _truth_audit(document_id, session)
    persist = _persistence_audit(document_id, session)
    return _build_timeline(doc, logs, retrieval, truth, persist)


# ---------------------------------------------------------------------------
# GET /{id}/explain — human-readable decision explanation
# ---------------------------------------------------------------------------


@router.get("/{document_id}/explain")
def explain_document(document_id: str, session: Session = Depends(get_db)) -> dict:
    """Return a structured human-readable explanation of pipeline decisions.

    Aggregates existing artifacts only — no new inference.
    """
    doc = _doc_or_404(document_id, session)
    truth = _truth_audit(document_id, session)
    persist = _persistence_audit(document_id, session)

    verdict = doc.status
    confidence_final = None
    verifiers_passed: list[str] = []
    verifiers_failed: list[str] = []
    fields_missing: list[str] = []
    fields_additional: list[str] = []
    truth_reason = None
    coverage_score = None

    if truth is not None:
        confidence_final = truth.final_confidence
        truth_reason = truth.decision_reason
        coverage_score = truth.coverage_score
        fields_missing = truth.required_fields_missing or []
        fields_additional = truth.additional_fields or []
        for vr in truth.verification_reports or []:
            name = vr.get("verifier_name", "unknown")
            if vr.get("passed") is True:
                verifiers_passed.append(name)
            elif vr.get("passed") is False:
                verifiers_failed.append(name)

    planner_reason = None
    learning_summary = None
    schema_proposal = None
    if persist is not None:
        planner_reason = persist.resolution_reason
        schema_proposal = persist.schema_proposal_json
        if persist.allow_learning:
            if persist.learn_from_correction:
                learning_action = "learned_from_human_correction"
            elif persist.learn_from_document:
                learning_action = "learned_from_document"
            else:
                learning_action = "learning_allowed_but_not_applied"
        else:
            learning_action = "learning_blocked"

        learning_summary = {
            "action": learning_action,
            "reason": persist.learning_reason,
            "schema_candidate": persist.schema_candidate,
        }

    return {
        "document_id": document_id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "verdict": verdict,
        "confidence": {
            "final": confidence_final,
            "coverage_score": coverage_score,
        },
        "truth_engine_reason": truth_reason,
        "verifiers": {
            "passed": verifiers_passed,
            "failed": verifiers_failed,
        },
        "field_coverage": {
            "missing_required": fields_missing,
            "additional_discovered": fields_additional,
        },
        "planner_reasoning": planner_reason,
        "learning": learning_summary,
        "schema_proposal": schema_proposal,
    }
