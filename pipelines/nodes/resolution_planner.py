from __future__ import annotations

import logging

from pipelines.resolution.planner import ResolutionPlanner
from pipelines.state import GraphState

log = logging.getLogger(__name__)

_planner = ResolutionPlanner()


def resolution_planner_node(state: GraphState) -> dict:
    """Consume TruthReport → produce ResolutionDecision.

    Passes retry_count and the accumulated execution_history so the planner
    has full context about previous autonomous attempts before deciding.
    """
    truth_report = state.get("truth_report")
    retry_count = state.get("retry_count", 0) or 0
    history = list(state.get("execution_history", []) or [])

    decision = _planner.plan(truth_report, retry_count, history)

    log.info(
        "event=ResolutionPlanned strategy=%s reason=%s requires_human=%s retry_count=%d",
        decision.strategy.value,
        decision.reason,
        decision.requires_human,
        retry_count,
    )
    return {"resolution_decision": decision}
