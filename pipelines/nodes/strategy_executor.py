from __future__ import annotations

import logging

from pipelines.resolution.executor import StrategyExecutor
from pipelines.state import GraphState

log = logging.getLogger(__name__)

_executor = StrategyExecutor()


def strategy_executor_node(state: GraphState) -> dict:
    """Execute the strategy chosen by resolution_planner_node.

    For RETRY: records the attempt; op_a_retry_node runs via the graph edge.
    For ACCEPT / HITL / REJECT: records the decision; downstream nodes handle the work.
    For unimplemented future strategies: raises NotImplementedError.

    Returns execution_history entries (a list) which LangGraph's operator.add
    reducer appends to the accumulated execution_history in GraphState.
    """
    decision = state["resolution_decision"]
    truth_report = state.get("truth_report")
    confidence = truth_report.final_confidence if truth_report else 0.0

    records = _executor.execute(decision, confidence)

    log.info(
        "event=StrategyExecuted strategy=%s outcome=%s confidence_before=%.4f",
        decision.strategy.value,
        records[0].outcome if records else "none",
        confidence,
    )
    return {"execution_history": records}
