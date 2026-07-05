"""Analytics endpoint — GET /analytics.

Aggregates pipeline metrics from existing DB tables.
Read-only. No inference. No planner changes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.deps import get_db
from db.models import ConfidenceLog, Document, PersistenceAuditLog, TruthAuditLog

router = APIRouter()


@router.get("/")
def get_analytics(session: Session = Depends(get_db)) -> dict:
    """Return aggregate pipeline metrics across all documents."""
    # --- Document counts by status ---
    status_rows = (
        session.query(Document.status, func.count(Document.id)).group_by(Document.status).all()
    )
    counts_by_status: dict[str, int] = {status: int(cnt) for status, cnt in status_rows}
    total = sum(counts_by_status.values())
    completed = counts_by_status.get("completed", 0)
    rejected = counts_by_status.get("rejected", 0)
    failed = counts_by_status.get("failed", 0)
    persist_failed = counts_by_status.get("persist_failed", 0)
    awaiting_review = counts_by_status.get("awaiting_review", 0)

    # --- Acceptance / HITL rates from PersistenceAuditLog ---
    total_with_audit = int(session.query(func.count(PersistenceAuditLog.id)).scalar() or 0)
    hitl_count = int(
        session.query(func.count(PersistenceAuditLog.id))
        .filter(PersistenceAuditLog.resolution_requires_human.is_(True))
        .scalar()
        or 0
    )
    accept_count = int(
        session.query(func.count(PersistenceAuditLog.id))
        .filter(PersistenceAuditLog.resolution_strategy == "accept")
        .scalar()
        or 0
    )

    # Retry rate: docs where extract ConfidenceLog appears more than once
    # (proxy for "went through at least one retry pass")
    retry_doc_count = int(
        session.query(func.count(func.distinct(ConfidenceLog.document_id)))
        .filter(ConfidenceLog.agent == "extract")
        .having(func.count(ConfidenceLog.id) > 1)
        .scalar()
        or 0
    )

    denom = total_with_audit if total_with_audit > 0 else 1
    doc_denom = total if total > 0 else 1

    # --- Strategy distribution ---
    strategy_rows = (
        session.query(PersistenceAuditLog.resolution_strategy, func.count(PersistenceAuditLog.id))
        .filter(PersistenceAuditLog.resolution_strategy.isnot(None))
        .group_by(PersistenceAuditLog.resolution_strategy)
        .all()
    )
    strategy_usage: dict[str, int] = {s: int(c) for s, c in strategy_rows}

    # --- Average confidence by agent ---
    conf_rows = (
        session.query(ConfidenceLog.agent, func.avg(ConfidenceLog.score))
        .group_by(ConfidenceLog.agent)
        .all()
    )
    avg_confidence: dict[str, float] = {
        agent: round(float(avg_score), 4) for agent, avg_score in conf_rows if avg_score is not None
    }

    # --- Verifier failure counts from TruthAuditLog ---
    # TruthAuditLog.verification_reports is JSON: [{verifier_name, passed, ...}]
    # We can't efficiently aggregate JSON in SQLAlchemy without raw SQL,
    # so we fetch and aggregate in Python. Capped at 5000 rows to avoid full scan.
    verifier_failures: dict[str, int] = {}
    audit_rows = session.query(TruthAuditLog.verification_reports).limit(5000).all()
    for (vr_list,) in audit_rows:
        for vr in vr_list or []:
            if vr.get("passed") is False:
                name = vr.get("verifier_name", "unknown")
                verifier_failures[name] = verifier_failures.get(name, 0) + 1

    # --- Schema candidate count ---
    schema_candidates = int(
        session.query(func.count(PersistenceAuditLog.id))
        .filter(PersistenceAuditLog.schema_candidate.is_(True))
        .scalar()
        or 0
    )

    return {
        "totals": {
            "documents": total,
            "completed": completed,
            "rejected": rejected,
            "failed": failed,
            "persist_failed": persist_failed,
            "awaiting_review": awaiting_review,
            "by_status": counts_by_status,
        },
        "rates": {
            "acceptance_rate": round(accept_count / denom, 4),
            "hitl_rate": round(hitl_count / denom, 4),
            "retry_rate": round(retry_doc_count / doc_denom, 4),
            "schema_candidate_rate": round(schema_candidates / denom, 4),
        },
        "strategy_usage": strategy_usage,
        "verifier_failures": verifier_failures,
        "avg_confidence": avg_confidence,
    }
