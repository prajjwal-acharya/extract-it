from config.settings import settings
from pipelines.state import GraphState


def route_after_truth(state: GraphState) -> str:
    """Route after truth_engine_node based on TruthReport.final_confidence.

    All confidence arithmetic lives in ConfidenceFusionPolicy — the router
    only reads the already-fused final_confidence and retry_count.

    NORMALIZE  — allow_completion and confidence >= threshold
    OP_A_RETRY — confidence below threshold, retries remain
    OP_B_HITL  — confidence below threshold, retries exhausted
    """
    truth_report = state.get("truth_report")
    if truth_report is None:
        return "op_b_hitl"

    if (
        truth_report.persistence.allow_completion
        and truth_report.final_confidence >= settings.CONFIDENCE_THRESHOLD
    ):
        return "normalize"
    if state.get("retry_count", 0) < settings.MAX_RETRIES:
        return "op_a_retry"
    return "op_b_hitl"


def route_after_hitl(state: GraphState) -> str:
    """Return the next node name after the HITL node."""
    return "normalize" if state.get("hitl_approved") else "persist"
