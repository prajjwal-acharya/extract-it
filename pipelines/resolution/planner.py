from __future__ import annotations

from config.settings import settings
from pipelines.resolution.models import ExecutionRecord, ResolutionDecision, RetryPlan, Strategy
from pipelines.truth_engine.models import TruthReport


class ResolutionPlanner:
    """Decides what to do after receiving a TruthReport.

    Inputs:  TruthReport, retry_count, execution_history
    Output:  ResolutionDecision

    Decision rules (in priority order):
      1. No TruthReport                      → HITL (safe fallback)
      2. document_status == "completed"      → ACCEPT
      3. Retries remain                      → RETRY
      4. Retries exhausted                   → HITL

    The planner reads only TruthReport fields and retry state — it has no
    access to raw confidence values, verification reports, or routing flags.
    All business logic that produced document_status lives in PersistenceDecision.
    """

    def __init__(self, max_retries: int = settings.MAX_RETRIES) -> None:
        self._max_retries = max_retries

    def plan(
        self,
        truth_report: TruthReport | None,
        retry_count: int,
        execution_history: list[ExecutionRecord],
    ) -> ResolutionDecision:
        if truth_report is None:
            return ResolutionDecision(
                strategy=Strategy.HITL,
                reason="no_truth_report",
                requires_human=True,
            )

        doc_status = truth_report.persistence.document_status

        if doc_status == "completed":
            return ResolutionDecision(
                strategy=Strategy.ACCEPT,
                reason=f"truth_engine_accepted:{truth_report.persistence.reason}",
                requires_human=False,
                learning_candidate=True,
            )

        if retry_count < self._max_retries:
            retry_plan = RetryPlan(
                attempt_number=retry_count + 1,
                reason=truth_report.persistence.reason,
                retrieval_strategy="similarity_search",
                prompt_strategy="standard",
            )
            return ResolutionDecision(
                strategy=Strategy.RETRY,
                reason=(
                    f"attempt_{retry_count + 1}_of_{self._max_retries}"
                    f":{truth_report.persistence.reason}"
                ),
                requires_human=False,
                retry_plan=retry_plan,
            )

        return ResolutionDecision(
            strategy=Strategy.HITL,
            reason=(
                f"retries_exhausted:{retry_count}_attempts"
                f":{truth_report.persistence.reason}"
            ),
            requires_human=True,
        )
