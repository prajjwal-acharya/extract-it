from __future__ import annotations

from datetime import datetime, timezone

from pipelines.resolution.models import ExecutionRecord, ResolutionDecision, Strategy

_UNIMPLEMENTED: frozenset[Strategy] = frozenset(
    {
        Strategy.PROMPT_REFINEMENT,
        Strategy.BETTER_RETRIEVAL,
        Strategy.IMAGE_PREPROCESS,
        Strategy.MODEL_ESCALATION,
    }
)

_OUTCOME: dict[Strategy, str] = {
    Strategy.ACCEPT: "accepted",
    Strategy.RETRY: "retry_scheduled",
    Strategy.HITL: "hitl_required",
    Strategy.REJECT: "rejected",
}


class StrategyExecutor:
    """Executes the strategy chosen by ResolutionPlanner.

    Only RETRY is fully implemented in Phase 5.1. For RETRY, the executor
    records the attempt and the graph router sends control to op_a_retry_node
    (which contains the existing retry implementation — unchanged from Phase 4).

    ACCEPT, HITL, and REJECT are recorded; actual work happens in downstream
    graph nodes (normalize, op_b_hitl, persist).

    Future strategies raise NotImplementedError. They are architecture
    placeholders and must not be called until their phase is implemented.
    """

    def execute(
        self,
        decision: ResolutionDecision,
        confidence_before: float,
    ) -> list[ExecutionRecord]:
        """Record the execution attempt and return the new history entries.

        Returns a list so LangGraph's operator.add reducer appends it to
        the accumulated execution_history in GraphState.
        """
        if decision.strategy in _UNIMPLEMENTED:
            raise NotImplementedError(
                f"Strategy {decision.strategy.value!r} is reserved for a future phase "
                "and has not been implemented yet."
            )
        record = ExecutionRecord(
            strategy=decision.strategy,
            timestamp=datetime.now(timezone.utc).isoformat(),
            outcome=_OUTCOME.get(decision.strategy, "unknown"),
            confidence_before=confidence_before,
            confidence_after=confidence_before if decision.strategy == Strategy.ACCEPT else None,
        )
        return [record]
