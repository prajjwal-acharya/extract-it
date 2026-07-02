from agents.validate_agent import meets_threshold
from config.settings import settings
from pipelines.state import GraphState


def route_after_validate(state: GraphState) -> str:
    """Return the next node name after the validate node."""
    if meets_threshold(state["validate_confidence"]):
        return "normalize"
    if state["retry_count"] < settings.MAX_RETRIES:
        return "op_a_retry"
    return "op_b_hitl"


def route_after_hitl(state: GraphState) -> str:
    """Return the next node name after the HITL node."""
    return "normalize" if state.get("hitl_approved") else "end"
