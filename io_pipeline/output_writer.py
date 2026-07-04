import json

from adapters.factory import get_object_store
from agents.llm_client import embed
from db.models import ConfidenceLog, Document
from db.session import get_session
from db.vector_store import upsert_embedding
from pipelines.state import GraphState


def _compute_status(state: GraphState) -> str:
    if state.get("error"):
        return "failed"
    if state.get("hitl_required") and not state.get("hitl_approved"):
        return "rejected"
    return "completed"


def write_output(state: GraphState) -> None:
    """Persist pipeline results to Postgres and the object store."""
    session = get_session()
    status = _compute_status(state)

    doc = session.get(Document, state["document_id"])
    assert doc is not None, f"Document {state['document_id']} not found"
    doc.status = status
    doc.current_phase = status  # completed / failed / rejected overrides "finalizing" stamp
    doc.universal_schema = state.get("universal_schema") or {}
    doc.extracted_fields = state.get("extracted_fields") or {}

    for agent, confidence in (
        ("classify", state.get("classify_confidence")),
        ("extract", state.get("extract_confidence")),
        ("validate", state.get("validate_confidence")),
    ):
        if confidence is not None:
            session.add(
                ConfidenceLog(
                    document_id=state["document_id"],
                    agent=agent,
                    score=confidence,
                    reason=state.get("error")
                    or "; ".join(state.get("validation_issues") or [])
                    or None,
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

    if state.get("verification_passed") is not None:
        passed = bool(state["verification_passed"])
        session.add(
            ConfidenceLog(
                document_id=state["document_id"],
                agent="verify",
                score=1.0 if passed else 0.0,
                reason=None if passed else "deterministic verifier check failed",
            )
        )

    session.commit()

    store = get_object_store()
    payload = json.dumps(state.get("universal_schema") or {}).encode()
    store.put(f"output/{state['document_id']}.json", payload, content_type="application/json")

    if status == "completed" and state.get("extracted_fields"):
        chunk_text = json.dumps(state["extracted_fields"])
        embedding = embed(chunk_text)
        upsert_embedding(session, state["document_id"], 0, chunk_text, embedding)
