import json
import logging

from adapters.factory import get_object_store
from agents.llm_client import embed
from db.models import (
    ConfidenceLog,
    Document,
    PersistenceAuditLog,
    SchemaProposalRecord,
    TruthAuditLog,
)
from db.session import get_session
from db.vector_store import upsert_embedding
from pipelines.learning.policy import LearningDecision, LearningPolicy
from pipelines.resolution.models import ResolutionDecision
from pipelines.state import GraphState
from pipelines.truth_engine.models import TruthReport

log = logging.getLogger(__name__)

_learning_policy = LearningPolicy()


def _compute_terminal_status(state: GraphState, truth_report: TruthReport | None) -> str:
    # Human rejection is final.
    if state.get("hitl_required") and not state.get("hitl_approved"):
        return "rejected"
    # Human approval overrides automated verifier failures — the reviewer has
    # accepted responsibility for the document's correctness.
    if state.get("hitl_required") and state.get("hitl_approved"):
        return "completed"
    # A truth report that explicitly allows completion takes precedence over stale
    # error flags (e.g. from a failed first attempt that succeeded on retry).
    if truth_report is not None and truth_report.persistence.allow_completion:
        return truth_report.persistence.document_status
    if state.get("error"):
        return "failed"
    if truth_report is not None:
        return truth_report.persistence.document_status
    return "failed"


def _compute_learning_decision(
    state: GraphState,
    truth_report: TruthReport | None,
    resolution_decision: ResolutionDecision | None,
) -> LearningDecision | None:
    if truth_report is None or resolution_decision is None:
        return None
    return _learning_policy.evaluate(
        resolution_decision,
        truth_report,
        list(state.get("execution_history") or []),
        is_human_correction=bool(state.get("hitl_correction", False)),
    )


def _write_confidence_logs(session, state: GraphState, truth_report: TruthReport | None) -> None:
    for agent, confidence in (
        ("classify", state.get("classify_confidence")),
        ("extract", state.get("extract_confidence")),
    ):
        if confidence is not None:
            session.add(
                ConfidenceLog(
                    document_id=state["document_id"],
                    agent=agent,
                    score=confidence,
                    reason=state.get("error") or None,
                )
            )

    if truth_report is not None:
        session.add(
            ConfidenceLog(
                document_id=state["document_id"],
                agent="truth_engine",
                score=truth_report.final_confidence,
                reason=truth_report.decision_reason,
            )
        )

    if state.get("schema_version") is not None:
        session.add(
            ConfidenceLog(
                document_id=state["document_id"],
                agent="schema_diff",
                score=1.0,
                reason=f"active schema version: {state['schema_version']}",
            )
        )


def _write_truth_audit(session, state: GraphState, truth_report: TruthReport) -> None:
    session.add(
        TruthAuditLog(
            document_id=state["document_id"],
            doc_type=state.get("doc_type"),
            final_confidence=truth_report.final_confidence,
            decision_reason=truth_report.decision_reason,
            coverage_score=truth_report.field_validation.coverage_score,
            required_fields_missing=truth_report.field_validation.required_fields_missing,
            additional_fields=truth_report.field_validation.additional_fields,
            verification_reports=[
                {
                    "verifier_name": r.verifier_name,
                    "passed": r.passed,
                    "confidence": r.confidence,
                    "details": r.details,
                }
                for r in truth_report.verification_reports
            ],
            document_status=truth_report.persistence.document_status,
            allow_completion=truth_report.persistence.allow_completion,
            allow_embedding=truth_report.persistence.allow_embedding,
            allow_learning=truth_report.persistence.allow_learning,
            persistence_reason=truth_report.persistence.reason,
            verifier_version=truth_report.verifier_version,
        )
    )


def _write_persistence_audit(
    session,
    state: GraphState,
    resolution_decision: ResolutionDecision | None,
    learning_decision: LearningDecision | None,
    schema_proposal_dict: dict | None,
    persist_status: str,
    persist_reason: str | None,
) -> None:
    session.add(
        PersistenceAuditLog(
            document_id=state["document_id"],
            resolution_strategy=resolution_decision.strategy.value if resolution_decision else None,
            resolution_reason=resolution_decision.reason if resolution_decision else None,
            resolution_requires_human=resolution_decision.requires_human
            if resolution_decision
            else False,
            learning_candidate=bool(resolution_decision.learning_candidate)
            if resolution_decision
            else False,
            allow_learning=learning_decision.allow_learning if learning_decision else False,
            learn_from_document=learning_decision.learn_from_document
            if learning_decision
            else False,
            learn_from_correction=learning_decision.learn_from_correction
            if learning_decision
            else False,
            schema_candidate=learning_decision.schema_candidate if learning_decision else False,
            learning_reason=learning_decision.reason if learning_decision else None,
            schema_proposal_json=(
                schema_proposal_dict
                if (learning_decision and learning_decision.schema_candidate)
                else None
            ),
            persist_status=persist_status,
            persist_reason=persist_reason,
        )
    )


def _write_schema_proposal_record(session, state: GraphState, schema_proposal_dict: dict) -> None:
    session.add(
        SchemaProposalRecord(
            doc_type=schema_proposal_dict.get("doc_type", ""),
            proposed_version=schema_proposal_dict.get("proposed_version", ""),
            additions_json=schema_proposal_dict.get("additions", []),
            relaxed_fields_json=schema_proposal_dict.get("relaxed_fields", []),
            origin_document_id=state.get("document_id"),
            status="pending",
        )
    )


def write_output(state: GraphState) -> None:
    """Persist pipeline results atomically to Postgres and the object store.

    Phases:
      A — DB audit rows (confidence logs, truth audit, persistence audit, schema proposal record)
      B — Object store JSON write
      C — Embedding upsert (gated on LearningDecision)
      D — Final status update + persist ConfidenceLog (agent="persist")

    Any failure in phases A–D rolls back what it can and transitions the document
    to persist_failed — a visible terminal state that is never silently swallowed.
    The persist ConfidenceLog is always attempted: score=1.0 on success, 0.0 on failure.
    """
    truth_report: TruthReport | None = state.get("truth_report")
    resolution_decision: ResolutionDecision | None = state.get("resolution_decision")
    schema_proposal_dict: dict | None = state.get("schema_proposal")

    terminal_status = _compute_terminal_status(state, truth_report)
    learning_decision = _compute_learning_decision(state, truth_report, resolution_decision)

    session = get_session()
    persist_reason: str | None = None

    try:
        # Phase A: Write audit rows.
        # Document status stays at "finalizing" (set by graph stamp) until Phase D.
        doc = session.get(Document, state["document_id"])
        if doc is None:
            raise ValueError(f"Document {state['document_id']} not found")

        doc.doc_type = state.get("doc_type") or doc.doc_type
        doc.universal_schema = state.get("universal_schema") or {}
        doc.extracted_fields = state.get("extracted_fields") or {}

        _write_confidence_logs(session, state, truth_report)
        if truth_report is not None:
            _write_truth_audit(session, state, truth_report)
        _write_persistence_audit(
            session,
            state,
            resolution_decision,
            learning_decision,
            schema_proposal_dict,
            persist_status=terminal_status,
            persist_reason=None,
        )
        if schema_proposal_dict and learning_decision and learning_decision.schema_candidate:
            _write_schema_proposal_record(session, state, schema_proposal_dict)

        session.commit()  # Phase A committed

        # Phase B: Object store.
        store = get_object_store()
        payload = json.dumps(state.get("universal_schema") or {}).encode()
        store.put(f"output/{state['document_id']}.json", payload, content_type="application/json")

        # Phase C: Embedding — LearningPolicy is sole authority.
        if learning_decision is not None and learning_decision.allow_learning:
            chunk_text = json.dumps(state.get("extracted_fields") or {})
            embedding_vec = embed(chunk_text)
            source = "hitl_correction" if learning_decision.learn_from_correction else "document"
            upsert_embedding(
                session, state["document_id"], 0, chunk_text, embedding_vec, source=source
            )

        # Phase D: Terminal status + persist signal.
        doc.status = terminal_status
        doc.current_phase = terminal_status
        session.add(
            ConfidenceLog(
                document_id=state["document_id"],
                agent="persist",
                score=1.0,
                reason=f"persist_success:{terminal_status}",
            )
        )
        session.commit()

    except Exception as exc:
        persist_reason = str(exc)
        log.error(
            "event=PersistFailed document_id=%s reason=%s",
            state.get("document_id"),
            persist_reason,
        )
        session.rollback()
        try:
            doc = session.get(Document, state["document_id"])
            if doc is not None:
                doc.status = "persist_failed"
                doc.current_phase = "persist_failed"
            session.add(
                ConfidenceLog(
                    document_id=state["document_id"],
                    agent="persist",
                    score=0.0,
                    reason=f"persist_failed:{persist_reason[:500]}",
                )
            )
            session.commit()
        except Exception:
            session.rollback()
        raise
    finally:
        session.close()
