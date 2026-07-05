from __future__ import annotations

import logging

from pipelines.resolution.executor import StrategyExecutor
from pipelines.resolution.models import Strategy
from pipelines.resolution.prompt_refinement import PromptRefinementStrategy
from pipelines.state import GraphState
from pipelines.truth_engine.models import TruthReport

log = logging.getLogger(__name__)

_executor = StrategyExecutor()
_refinement_strategy = PromptRefinementStrategy()


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

    For RETRY: records the attempt; op_a_retry_node runs next (unchanged).
    For PROMPT_REFINEMENT: generates a RefinedPrompt from TruthReport evidence,
      records the attempt with prompt_variant, and sets refined_prompt in state so
      op_a_retry_node can append the focused guidance to the base prompt.
    For ACCEPT / HITL / REJECT: records the decision; downstream nodes handle work.
    For unimplemented future strategies: raises NotImplementedError.

    refined_prompt is always written to state (None for non-refinement strategies)
    so it does not leak across pipeline passes from a prior PROMPT_REFINEMENT.

    execution_history returns a list; LangGraph's operator.add reducer appends it.
    """
    decision = state["resolution_decision"]
    truth_report = state.get("truth_report")
    confidence = truth_report.final_confidence if truth_report else 0.0
    evidence = _snapshot_evidence(truth_report)

    refined_prompt = None
    if decision.strategy == Strategy.PROMPT_REFINEMENT and truth_report is not None:
        refined_prompt = _refinement_strategy.generate(truth_report)
        log.info(
            "event=PromptRefined variant=%s target_fields=%s",
            refined_prompt.prompt_variant,
            refined_prompt.target_fields,
        )

    records = _executor.execute(decision, confidence, evidence_before=evidence)

    log.info(
        "event=StrategyExecuted strategy=%s outcome=%s confidence_before=%.4f",
        decision.strategy.value,
        records[0].outcome if records else "none",
        confidence,
    )
    return {"execution_history": records, "refined_prompt": refined_prompt}
