from __future__ import annotations

import logging

from pipelines.resolution.models import PlannerBundle
from pipelines.resolution.planner import ResolutionPlanner
from pipelines.state import GraphState

log = logging.getLogger(__name__)

_planner = ResolutionPlanner()


def resolution_planner_node(state: GraphState) -> dict:
    """Consume TruthReport → produce ResolutionDecision.

    Builds a PlannerBundle from GraphState so the planner receives exactly
    one immutable context object — no scattered state reads inside planner rules.
    """
    truth_report = state.get("truth_report")
    retry_count = state.get("retry_count", 0) or 0
    history = list(state.get("execution_history", []) or [])
    remaining_budget = max(0, _planner._max_retries - retry_count)

    bundle = PlannerBundle(
        truth_report=truth_report,
        execution_history=history,
        retry_count=retry_count,
        remaining_budget=remaining_budget,
    )

    decision = _planner.plan(bundle)

    log.info(
        "event=ResolutionPlanned strategy=%s reason=%r requires_human=%s "
        "retry_count=%d remaining_budget=%d",
        decision.strategy.value,
        decision.reason,
        decision.requires_human,
        retry_count,
        remaining_budget,
    )
    return {"resolution_decision": decision}
