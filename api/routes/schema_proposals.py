"""Schema proposal approval workflow.

Pending proposals are created by write_output when LearningDecision.schema_candidate
is True.  A human must explicitly approve a proposal before apply_diff() activates
the new SchemaVersion — there is no auto-apply path.

Approval workflow:
  GET  /schema-proposals/pending       — list pending proposals
  POST /schema-proposals/{id}/approve  — approve → apply_diff → new SchemaVersion
  POST /schema-proposals/{id}/reject   — reject with reason (proposal stays auditable)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.schema_diff_agent import SchemaDiff, apply_diff
from api.deps import get_db
from config.settings import settings
from db.models import SchemaProposalRecord, SchemaVersion

router = APIRouter()

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _require_api_key(key: str | None = Depends(_api_key_header)) -> None:
    if not settings.REVIEW_API_KEY:
        return
    if key != settings.REVIEW_API_KEY:
        raise HTTPException(401, "Invalid or missing X-API-Key")


@router.get("/pending")
def list_pending_proposals(session: Session = Depends(get_db)) -> list[dict]:
    """Return all proposals awaiting human review."""
    rows = (
        session.execute(
            select(SchemaProposalRecord)
            .where(SchemaProposalRecord.status == "pending")
            .order_by(SchemaProposalRecord.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "doc_type": r.doc_type,
            "proposed_version": r.proposed_version,
            "additions": r.additions_json,
            "relaxed_fields": r.relaxed_fields_json,
            "origin_document_id": r.origin_document_id,
            "status": r.status,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


class RejectRequest(BaseModel):
    reason: str


@router.post("/{proposal_id}/approve", dependencies=[Depends(_require_api_key)])
def approve_proposal(proposal_id: str, session: Session = Depends(get_db)) -> dict:
    """Approve a pending schema proposal and activate the new SchemaVersion."""
    proposal = session.get(SchemaProposalRecord, proposal_id)
    if proposal is None:
        raise HTTPException(404, f"Proposal {proposal_id!r} not found")
    if proposal.status != "pending":
        raise HTTPException(409, f"Proposal is already {proposal.status!r}")

    active_row = session.execute(
        select(SchemaVersion).where(
            SchemaVersion.doc_type == proposal.doc_type,
            SchemaVersion.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if active_row is None:
        raise HTTPException(422, f"No active SchemaVersion for doc_type={proposal.doc_type!r}")

    diff = SchemaDiff(
        additions=proposal.additions_json or [],
        relaxed_fields=proposal.relaxed_fields_json or [],
    )
    new_version = apply_diff(
        session, active_row, diff, origin_document_id=proposal.origin_document_id or ""
    )

    proposal.status = "approved"
    proposal.approved_schema_version = new_version.version
    proposal.updated_at = datetime.now(timezone.utc)
    session.commit()

    return {
        "proposal_id": proposal_id,
        "status": "approved",
        "new_schema_version": new_version.version,
        "doc_type": proposal.doc_type,
    }


@router.post("/{proposal_id}/reject", dependencies=[Depends(_require_api_key)])
def reject_proposal(
    proposal_id: str, body: RejectRequest, session: Session = Depends(get_db)
) -> dict:
    """Reject a pending proposal. The row is retained for audit."""
    proposal = session.get(SchemaProposalRecord, proposal_id)
    if proposal is None:
        raise HTTPException(404, f"Proposal {proposal_id!r} not found")
    if proposal.status != "pending":
        raise HTTPException(409, f"Proposal is already {proposal.status!r}")

    proposal.status = "rejected"
    proposal.rejection_reason = body.reason
    proposal.updated_at = datetime.now(timezone.utc)
    session.commit()

    return {"proposal_id": proposal_id, "status": "rejected"}
