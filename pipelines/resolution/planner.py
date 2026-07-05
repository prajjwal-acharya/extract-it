from __future__ import annotations

from config.settings import settings
from pipelines.resolution.models import ExecutionRecord, PlannerBundle, ResolutionDecision, RetryPlan, Strategy
from pipelines.resolution.prompt_refinement import failure_variant
from pipelines.truth_engine.models import TruthReport


# Autonomous strategies tried in order before falling back to generic RETRY.
# Each is attempted once per failure-variant (variant-specific) or once per
# document pass (variant-agnostic), controlled by the dedup helpers below.
_AUTONOMOUS_STRATEGY_ORDER: list[Strategy] = [
    Strategy.PROMPT_REFINEMENT,    # variant-specific: different prompt per failure type
    Strategy.BETTER_RETRIEVAL,     # variant-specific: targeted RAG per failure type
    Strategy.IMAGE_PREPROCESS,     # variant-agnostic: once per document pass
    Strategy.MODEL_ESCALATION,     # variant-agnostic: once per document pass
]

# Strategies that are deduplicated by (strategy, failure_variant).
# Others are deduplicated by strategy alone (tried at most once regardless of variant).
_VARIANT_DEDUPED: frozenset[Strategy] = frozenset(
    {Strategy.PROMPT_REFINEMENT, Strategy.BETTER_RETRIEVAL}
)


class ResolutionPlanner:
    """Evidence-driven planner that decides the next action after a TruthReport.

    Input:  PlannerBundle (truth_report, execution_history, retry_count, remaining_budget)
    Output: ResolutionDecision

    The planner evaluates raw evidence from TruthReport — NOT the pre-computed
    document_status. document_status is an output of the Truth Engine's
    confidence fusion; the planner re-derives the decision from evidence so
    that extending recovery requires no new rule blocks.

    Rule priority (evaluated in order, first match wins):
      1. No TruthReport available              → HITL (safe fallback)
      2. All signals green                     → ACCEPT (evaluated before budget check
                                                  so a successful extraction after the
                                                  last retry is still accepted, not HITL'd)
      3. Retry budget exhausted                → HITL
      4. Next untried autonomous strategy      → that strategy (cycles through
                                                  PROMPT_REFINEMENT → BETTER_RETRIEVAL →
                                                  IMAGE_PREPROCESS → MODEL_ESCALATION)
      5. All autonomous strategies exhausted  → RETRY (generic, standard prompt)

    Adding a new autonomous strategy: append it to _AUTONOMOUS_STRATEGY_ORDER and
    add it to _VARIANT_DEDUPED if it is failure-variant-specific. No new rule blocks.
    The interface (PlannerBundle → ResolutionDecision) is unchanged.
    """

    def __init__(
        self,
        max_retries: int = settings.MAX_RETRIES,
        confidence_threshold: float = settings.CONFIDENCE_THRESHOLD,
        coverage_threshold: float = 0.80,
    ) -> None:
        self._max_retries = max_retries
        self._confidence_threshold = confidence_threshold
        self._coverage_threshold = coverage_threshold

    # ------------------------------------------------------------------ public

    def plan(self, bundle: PlannerBundle) -> ResolutionDecision:
        """Evaluate evidence and return the next ResolutionDecision."""
        truth_report = bundle.truth_report

        # Rule 1: No evidence
        if truth_report is None:
            return ResolutionDecision(
                strategy=Strategy.HITL,
                reason="No truth report available — cannot make autonomous decision.",
                requires_human=True,
            )

        # Rule 2: Acceptance (before budget check)
        failed_verifiers = self._failed_verifiers(truth_report)
        above_threshold = truth_report.final_confidence >= self._confidence_threshold
        sufficient_coverage = (
            truth_report.field_validation.coverage_score >= self._coverage_threshold
        )

        if not failed_verifiers and above_threshold and sufficient_coverage:
            return ResolutionDecision(
                strategy=Strategy.ACCEPT,
                reason=(
                    f"All signals accepted: "
                    f"confidence={truth_report.final_confidence:.4f} "
                    f"(threshold={self._confidence_threshold:.2f}), "
                    f"coverage={truth_report.field_validation.coverage_score:.2%}, "
                    f"all verifiers passed."
                ),
                requires_human=False,
                learning_candidate=True,
            )

        # Rule 3: Budget exhausted
        if bundle.remaining_budget <= 0:
            return ResolutionDecision(
                strategy=Strategy.HITL,
                reason=(
                    f"Retry budget exhausted after {bundle.retry_count} attempt"
                    f"{'s' if bundle.retry_count != 1 else ''}."
                ),
                requires_human=True,
            )

        # Rule 4: Next untried autonomous strategy
        failure_reason = self._classify_failure(truth_report)
        variant = failure_variant(truth_report, self._coverage_threshold)
        next_strategy = self._next_autonomous_strategy(bundle.execution_history, variant)

        if next_strategy is not None:
            prior_variants = [
                r.strategy_metadata.get("prompt_variant", "")
                for r in bundle.execution_history
                if r.strategy == Strategy.PROMPT_REFINEMENT
            ]
            prompt_strategy = "refined" if next_strategy == Strategy.PROMPT_REFINEMENT else "standard"
            retry_plan = RetryPlan(
                attempt_number=bundle.retry_count + 1,
                reason=failure_reason,
                retrieval_strategy="similarity_search",
                prompt_strategy=prompt_strategy,
                refinement_reason=failure_reason if next_strategy == Strategy.PROMPT_REFINEMENT else None,
                prompt_variant=variant if next_strategy in _VARIANT_DEDUPED else None,
                refinement_history=[v for v in prior_variants if v],
            )
            strategy_label = next_strategy.value.replace("_", " ").title()
            return ResolutionDecision(
                strategy=next_strategy,
                reason=f"{strategy_label} scheduled: {failure_reason}",
                requires_human=False,
                retry_plan=retry_plan,
            )

        # Rule 5: Generic RETRY — all autonomous strategies exhausted
        retry_plan = RetryPlan(
            attempt_number=bundle.retry_count + 1,
            reason=failure_reason,
            retrieval_strategy="similarity_search",
            prompt_strategy="standard",
        )
        return ResolutionDecision(
            strategy=Strategy.RETRY,
            reason=failure_reason,
            requires_human=False,
            retry_plan=retry_plan,
        )

    # --------------------------------------------------------------- private

    def _next_autonomous_strategy(
        self, history: list[ExecutionRecord], variant: str
    ) -> Strategy | None:
        """Return the first autonomous strategy not yet tried for this failure pattern.

        Variant-specific strategies (PROMPT_REFINEMENT, BETTER_RETRIEVAL) are
        deduplicated by (strategy, variant). Variant-agnostic strategies
        (IMAGE_PREPROCESS, MODEL_ESCALATION) are deduplicated by strategy alone.
        """
        for strategy in _AUTONOMOUS_STRATEGY_ORDER:
            if strategy in _VARIANT_DEDUPED:
                tried = self._tried_variant(history, strategy, variant)
            else:
                tried = self._tried_ever(history, strategy)
            if not tried:
                return strategy
        return None

    @staticmethod
    def _tried_variant(
        history: list[ExecutionRecord], strategy: Strategy, variant: str
    ) -> bool:
        return any(
            r.strategy == strategy and r.strategy_metadata.get("prompt_variant") == variant
            for r in history
        )

    @staticmethod
    def _tried_ever(history: list[ExecutionRecord], strategy: Strategy) -> bool:
        return any(r.strategy == strategy for r in history)

    @staticmethod
    def _failed_verifiers(truth_report: TruthReport) -> list[str]:
        return [r.verifier_name for r in truth_report.verification_reports if r.passed is False]

    def _classify_failure(self, truth_report: TruthReport) -> str:
        """Return a human-readable explanation of what evidence triggered recovery."""
        failed = self._failed_verifiers(truth_report)
        if failed:
            return f"Deterministic verification failed: [{', '.join(failed)}]."

        missing = truth_report.field_validation.required_fields_missing
        if missing:
            shown = missing[:3]
            tail = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
            return f"Required fields missing after extraction: [{', '.join(shown)}{tail}]."

        cov = truth_report.field_validation.coverage_score
        if cov < self._coverage_threshold:
            return (
                f"Insufficient schema coverage: {cov:.2%} "
                f"(threshold={self._coverage_threshold:.2%})."
            )

        return (
            f"Extraction confidence below threshold: "
            f"{truth_report.final_confidence:.4f} < {self._confidence_threshold:.2f}."
        )
