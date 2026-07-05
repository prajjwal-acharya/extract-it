from __future__ import annotations

from config.settings import settings
from pipelines.resolution.models import ExecutionRecord, PlannerBundle, ResolutionDecision, RetryPlan, Strategy
from pipelines.resolution.prompt_refinement import failure_variant
from pipelines.truth_engine.models import TruthReport, VerificationReport


class ResolutionPlanner:
    """Evidence-driven planner that decides the next action after a TruthReport.

    Input:  PlannerBundle (truth_report, execution_history, retry_count, remaining_budget)
    Output: ResolutionDecision

    The planner evaluates raw evidence from TruthReport — NOT the pre-computed
    document_status. document_status is an output of the Truth Engine's
    confidence fusion; the planner re-derives the decision from evidence so
    that adding a new strategy only requires adding a new rule here.

    Rule priority (evaluated in order, first match wins):
      1. No TruthReport available              → HITL (safe fallback)
      2. All signals green                     → ACCEPT (evaluated before budget — a
                                                  successful extraction after the last
                                                  retry is still accepted, not HITL'd)
      3. Retry budget exhausted                → HITL
      4. Failure is refineable, not yet tried  → PROMPT_REFINEMENT (Phase 5.3)
      5. Deterministic verifier failure        → RETRY (refinement already tried)
      6. Required fields missing               → RETRY (refinement already tried)
      7. Schema coverage insufficient          → RETRY (refinement already tried)
      8. Extraction confidence too low         → RETRY (refinement already tried)

    To add a new autonomous strategy: insert a new rule between 4 and 5 following
    the same pattern — check history for duplicates, return the new Strategy.
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

        # Rule 2: Acceptance — all evidence signals are green (evaluated before budget check
        # so a successful extraction after exhausting retries is still accepted).
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

        # Rule 3: Budget exhausted — document failed and no retries remain
        if bundle.remaining_budget <= 0:
            return ResolutionDecision(
                strategy=Strategy.HITL,
                reason=(
                    f"Retry budget exhausted after {bundle.retry_count} attempt"
                    f"{'s' if bundle.retry_count != 1 else ''}."
                ),
                requires_human=True,
            )

        # Rule 4: PROMPT_REFINEMENT — if this failure pattern has not been refined yet
        failure_reason = self._classify_failure(truth_report)
        variant = failure_variant(truth_report, self._coverage_threshold)
        already_refined = any(
            r.strategy == Strategy.PROMPT_REFINEMENT
            and r.strategy_metadata.get("prompt_variant") == variant
            for r in bundle.execution_history
        )
        if not already_refined:
            prior_variants = [
                r.strategy_metadata.get("prompt_variant", "")
                for r in bundle.execution_history
                if r.strategy == Strategy.PROMPT_REFINEMENT
            ]
            retry_plan = RetryPlan(
                attempt_number=bundle.retry_count + 1,
                reason=failure_reason,
                retrieval_strategy="similarity_search",
                prompt_strategy="refined",
                refinement_reason=failure_reason,
                prompt_variant=variant,
                refinement_history=[v for v in prior_variants if v],
            )
            return ResolutionDecision(
                strategy=Strategy.PROMPT_REFINEMENT,
                reason=f"Prompt refinement scheduled: {failure_reason}",
                requires_human=False,
                retry_plan=retry_plan,
            )

        # Rules 5–8: Generic RETRY — refinement was already tried for this pattern
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

    @staticmethod
    def _failed_verifiers(truth_report: TruthReport) -> list[str]:
        return [r.verifier_name for r in truth_report.verification_reports if r.passed is False]

    def _classify_failure(self, truth_report: TruthReport) -> str:
        """Return a human-readable explanation of what evidence triggered the retry.

        Priority mirrors the rule order so the most actionable failure is surfaced.
        """
        # Rule 4: Deterministic verification failure (highest priority)
        failed = self._failed_verifiers(truth_report)
        if failed:
            names = ", ".join(failed)
            return f"Deterministic verification failed: [{names}]."

        # Rule 5: Required fields missing
        missing = truth_report.field_validation.required_fields_missing
        if missing:
            shown = missing[:3]
            tail = f" and {len(missing) - 3} more" if len(missing) > 3 else ""
            return f"Required fields missing after extraction: [{', '.join(shown)}{tail}]."

        # Rule 6: Coverage insufficient
        cov = truth_report.field_validation.coverage_score
        if cov < self._coverage_threshold:
            return (
                f"Insufficient schema coverage: {cov:.2%} "
                f"(threshold={self._coverage_threshold:.2%})."
            )

        # Rule 7: Confidence too low
        return (
            f"Extraction confidence below threshold: "
            f"{truth_report.final_confidence:.4f} < {self._confidence_threshold:.2f}."
        )
