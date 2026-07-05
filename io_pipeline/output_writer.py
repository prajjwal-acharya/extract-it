import json

from adapters.factory import get_object_store
from agents.llm_client import embed
from db.models import ConfidenceLog, Document, TruthAuditLog
from db.session import get_session
from db.vector_store import upsert_embedding
from pipelines.learning.policy import LearningPolicy
from pipelines.state import GraphState
from pipelines.truth_engine.models import TruthReport

_learning_policy = LearningPolicy()


def write_output(state: GraphState) -> None:
    """Persist pipeline results to Postgres and the object store.

    Status is owned by TruthReport.persistence.document_status. Only HITL
    rejection and pipeline errors may override it. The writer carries no
    business logic of its own.

    Phase 5.5: embedding is now gated behind LearningPolicy.evaluate() rather
    than the raw allow_embedding flag.  LearningPolicy is the sole authority
    on whether a document (or human correction) may update the knowledge base.
    """
    truth_report: TruthReport | None = state.get("truth_report")

    # Minimal override layer — Truth Engine owns the business decision.
    if state.get("error"):
        status = "failed"
    elif state.get("hitl_required") and not state.get("hitl_approved"):
        status = "rejected"
    elif truth_report is not None:
        status = truth_report.persistence.document_status
    else:
        status = "failed"

    session = get_session()
    try:
        doc = session.get(Document, state["document_id"])
        if doc is None:
            raise ValueError(f"Document {state['document_id']} not found")
        doc.status = status
        doc.current_phase = status
        if state.get("doc_type"):
            doc.doc_type = state["doc_type"]
        doc.universal_schema = state.get("universal_schema") or {}
        doc.extracted_fields = state.get("extracted_fields") or {}

        # ConfidenceLog — classify and extract are always logged
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

        # Truth Engine confidence log
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

        # TruthAuditLog — full evidence bundle for audit replay and ML
        if truth_report is not None:
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

        session.commit()  # commit DB before object store write

        store = get_object_store()
        payload = json.dumps(state.get("universal_schema") or {}).encode()
        store.put(f"output/{state['document_id']}.json", payload, content_type="application/json")

        # Embedding gated on LearningPolicy (sole authority on knowledge-base updates).
        # LearningPolicy reads resolution_decision + truth_report + hitl_correction;
        # it enforces ACCEPT strategy, no verifier failures, and TE allow_learning.
        resolution_decision = state.get("resolution_decision")
        execution_history = list(state.get("execution_history") or [])
        is_correction = bool(state.get("hitl_correction", False))

        learning_decision = None
        if truth_report is not None and resolution_decision is not None:
            learning_decision = _learning_policy.evaluate(
                resolution_decision,
                truth_report,
                execution_history,
                is_human_correction=is_correction,
            )

        allow_embed = learning_decision.allow_learning if learning_decision is not None else False
        if allow_embed and state.get("extracted_fields"):
            chunk_text = json.dumps(state["extracted_fields"])
            embedding = embed(chunk_text)
            upsert_embedding(session, state["document_id"], 0, chunk_text, embedding)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
