import logging

from pipelines.state import GraphState

log = logging.getLogger(__name__)


def unknown_handler_node(state: GraphState) -> dict:
    """Terminal node for documents that cannot be routed to extraction.

    Handles both UNKNOWN (unrecognized type / low confidence) and FAILURE
    (agent call failed completely). Sets error so write_output computes
    status='failed'. No extraction, no normalization.
    """
    ctx = state.get("classification_context")
    if ctx is not None:
        plan = ctx.routing_plan
        reason = plan.reason
        log.warning(
            "event=RoutingFailed document_id=%s routing_version=%s action=%s "
            "doc_type=%s confidence=%.3f reason=%s",
            state.get("document_id"),
            plan.routing_version,
            plan.action.value,
            plan.document_type,
            plan.confidence,
            reason,
        )
    else:
        reason = "no_classification_context"
        log.warning(
            "event=RoutingFailed document_id=%s reason=%s",
            state.get("document_id"),
            reason,
        )

    return {"error": f"routing_failed: {reason}"}
