from __future__ import annotations

from config.settings import settings
from pipelines.resolution.models import ExecutionRecord, PlannerBundle, ResolutionDecision, RetryPlan, Strategy
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
      1. No TruthReport available         → HITL (safe fallback)
      2. Retry budget exhausted           → HITL
      3. All signals green                → ACCEPT
      4. Deterministic verifier failure   → RETRY
      5. Required fields missing          → RETRY
      6. Schema coverage insufficient     → RETRY
      7. Extraction confidence too low    → RETRY

    Future autonomous strategies (PROMPT_REFINEMENT, BETTER_RETRIEVAL, etc.)
    slot in between rules 4–7 without changing the interface or existing rules.
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

        # Rule 2: Budget exhausted — before evaluating evidence
        if bundle.remaining_budget <= 0:
            return ResolutionDecision(
                strategy=Strategy.HITL,
                reason=(
                    f"Retry budget exhausted after {bundle.retry_count} attempt"
                    f"{'s' if bundle.retry_count != 1 else ''}."
                ),
                requires_human=True,
            )

        # Rule 3: Acceptance — all evidence signals are green
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

        # Rules 4–7: Classify the failure and schedule a retry
        failure_reason = self._classify_failure(truth_report)
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
