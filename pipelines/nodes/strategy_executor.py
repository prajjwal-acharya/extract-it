from __future__ import annotations

import logging

from pipelines.resolution.executor import StrategyExecutor
from pipelines.state import GraphState
from pipelines.truth_engine.models import TruthReport

log = logging.getLogger(__name__)

_executor = StrategyExecutor()


def _snapshot_evidence(truth_report: TruthReport | None) -> dict | None:
    """Capture key metrics at decision time for ExecutionRecord.evidence_before."""
    if truth_report is None:
        return None
    return {
        "final_confidence": truth_report.final_confidence,
        "coverage_score": truth_report.field_validation.coverage_score,
        "required_fields_missing": truth_report.field_validation.required_fields_missing,
        "verifier_failures": [
            r.verifier_name
            for r in truth_report.verification_reports
            if r.passed is False
        ],
        "verifier_version": truth_report.verifier_version,
    }


def strategy_executor_node(state: GraphState) -> dict:
    """Execute the strategy chosen by resolution_planner_node.

    For RETRY: records the attempt with an evidence snapshot; op_a_retry_node
    runs next via the graph edge (existing retry implementation, unchanged).
    For ACCEPT / HITL / REJECT: records the decision; downstream nodes handle the work.
    For unimplemented future strategies: raises NotImplementedError.

    Returns execution_history entries (a list) which LangGraph's operator.add
    reducer appends to the accumulated execution_history in GraphState.
    """
    decision = state["resolution_decision"]
    truth_report = state.get("truth_report")
    confidence = truth_report.final_confidence if truth_report else 0.0
    evidence = _snapshot_evidence(truth_report)

    records = _executor.execute(decision, confidence, evidence_before=evidence)

    log.info(
        "event=StrategyExecuted strategy=%s outcome=%s confidence_before=%.4f",
        decision.strategy.value,
        records[0].outcome if records else "none",
        confidence,
    )
    return {"execution_history": records}
