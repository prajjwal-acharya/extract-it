from langgraph.types import interrupt
from pipelines.state import DocumentState


def op_b_hitl_node(state: DocumentState) -> dict:
    review_payload = {
        "document_id": state.document_id,
        "doc_type": state.doc_type,
        "extracted_fields": state.extracted_fields,
        "validation_issues": state.validation_issues,
        "confidence": state.validate_confidence,
    }
    human_decision = interrupt(review_payload)
    approved = human_decision.get("approved", False)
    corrections = human_decision.get("corrections", {})

    updated_fields = {**state.extracted_fields, **corrections}
    return {
        "hitl_required": True,
        "hitl_approved": approved,
        "extracted_fields": updated_fields,
        "status": "hitl_complete",
    }
