from __future__ import annotations

from datetime import datetime, timezone

from pipelines.resolution.models import ExecutionRecord, ResolutionDecision, Strategy

# Strategies still pending a future phase
_UNIMPLEMENTED: frozenset[Strategy] = frozenset()

_OUTCOME: dict[Strategy, str] = {
    Strategy.ACCEPT: "accepted",
    Strategy.RETRY: "retry_scheduled",
    Strategy.PROMPT_REFINEMENT: "refinement_scheduled",
    Strategy.BETTER_RETRIEVAL: "better_retrieval_scheduled",
    Strategy.IMAGE_PREPROCESS: "preprocess_scheduled",
    Strategy.MODEL_ESCALATION: "escalation_scheduled",
    Strategy.HITL: "hitl_required",
    Strategy.REJECT: "rejected",
}


class StrategyExecutor:
    """Records autonomous strategy execution attempts.

    The executor is responsible for:
      - Validating that the strategy is implemented
      - Building the ExecutionRecord (outcome, metadata, analytics)
      - Returning the record list for LangGraph's operator.add accumulator

    Strategy-specific side effects (building RefinedPrompt, retrieval queries,
    preprocessing bytes, model override) are handled by strategy_executor_node,
    which has full access to GraphState. The executor remains a pure recorder.
    """

    def execute(
        self,
        decision: ResolutionDecision,
        confidence_before: float,
        evidence_before: dict | None = None,
        directives: list[str] | None = None,
        model_used: str | None = None,
        retrieval_count: int = 0,
        preprocessing_steps: list[str] | None = None,
    ) -> list[ExecutionRecord]:
        """Record the execution attempt and return the new history entries.

        Additional analytics parameters capture telemetry produced by the node:
          directives         — Directive.value strings that drove the strategy
          model_used         — explicit model name if MODEL_ESCALATION, else None
          retrieval_count    — number of RAG chunks retrieved by BETTER_RETRIEVAL
          preprocessing_steps — ops applied by IMAGE_PREPROCESS
        """
        if decision.strategy in _UNIMPLEMENTED:
            raise NotImplementedError(
                f"Strategy {decision.strategy.value!r} is not yet implemented."
            )

        strategy_metadata: dict = {}
        if decision.retry_plan is not None:
            strategy_metadata = {
                "attempt_number": decision.retry_plan.attempt_number,
                "retrieval_strategy": decision.retry_plan.retrieval_strategy,
                "prompt_strategy": decision.retry_plan.prompt_strategy,
            }
            if decision.retry_plan.prompt_variant is not None:
                strategy_metadata["prompt_variant"] = decision.retry_plan.prompt_variant
            if decision.retry_plan.refinement_reason is not None:
                strategy_metadata["refinement_reason"] = decision.retry_plan.refinement_reason
            if decision.retry_plan.refinement_history:
                strategy_metadata["refinement_history"] = decision.retry_plan.refinement_history

        record = ExecutionRecord(
            strategy=decision.strategy,
            timestamp=datetime.now(timezone.utc).isoformat(),
            outcome=_OUTCOME.get(decision.strategy, "unknown"),
            confidence_before=confidence_before,
            confidence_after=confidence_before if decision.strategy == Strategy.ACCEPT else None,
            strategy_metadata=strategy_metadata,
            directives=directives or [],
            model_used=model_used,
            retrieval_count=retrieval_count,
            preprocessing_steps=preprocessing_steps or [],
            evidence_before=evidence_before,
        )
        return [record]
