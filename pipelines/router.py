from pipelines.resolution.models import Strategy
from pipelines.state import GraphState


def route_after_executor(state: GraphState) -> str:
    """Route after strategy_executor_node based on ResolutionDecision.strategy.

    Graph nodes must not inspect TruthReport internals. All downstream routing
    is owned by ResolutionPlanner; this function only dispatches on the result.

    ACCEPT   → normalize
    RETRY    → op_a_retry  (existing retry implementation, unchanged from Phase 4)
    HITL     → op_b_hitl
    REJECT   → persist     (final, skip normalization)
    future / None → op_b_hitl  (safe fallback for unimplemented strategies)
    """
    decision = state.get("resolution_decision")
    if decision is None:
        return "op_b_hitl"
    if decision.strategy == Strategy.ACCEPT:
        return "normalize"
    if decision.strategy in (Strategy.RETRY, Strategy.PROMPT_REFINEMENT):
        return "op_a_retry"
    if decision.strategy == Strategy.REJECT:
        return "persist"
    return "op_b_hitl"   # HITL and all unimplemented future strategies


def route_after_hitl(state: GraphState) -> str:
    """Return the next node name after the HITL node."""
    return "normalize" if state.get("hitl_approved") else "persist"
