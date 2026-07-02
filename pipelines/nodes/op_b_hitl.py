from langgraph.types import interrupt

from pipelines.state import GraphState


def op_b_hitl_node(state: GraphState) -> dict:
    """Pause the graph and surface extracted fields for human review.

    Resume payload shape: {"approved": bool, "corrections": dict | None}
    """
    decision = interrupt({
        "document_id": state["document_id"],
        "doc_type": state.get("doc_type"),
        "extracted_fields": state.get("extracted_fields"),
        "validation_issues": state.get("validation_issues"),
    })

    approved = bool(decision.get("approved"))
    corrections = decision.get("corrections") or {}
    merged_fields = {**(state.get("extracted_fields") or {}), **corrections}

    return {
        "hitl_required": True,
        "hitl_approved": approved,
        "extracted_fields": merged_fields,
    }
