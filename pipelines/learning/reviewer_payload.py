"""Reviewer payload for HITL nodes.

Replaces the old `validation_issues: list[str]` with a structured, evidence-driven
summary that gives human reviewers everything they need to correct or approve a
document without diving into raw logs.

Built from:
  - TruthReport  — field validation, verifier results, confidence breakdown
  - ResolutionDecision — planner reason for escalating to HITL
  - ExecutionHistory — what autonomous strategies were tried before HITL
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReviewerPayload:
    """Structured evidence surfaced to human reviewers during HITL interrupts.

    to_interrupt_payload() serialises to the dict passed to langgraph.types.interrupt()
    so the resume handler receives a fully structured payload rather than a
    flat validation_issues list.
    """

    document_id: str
    doc_type: str | None
    extracted_fields: dict

    # TruthReport evidence
    missing_required_fields: list[str] = field(default_factory=list)
    additional_fields: list[str] = field(default_factory=list)
    verifier_failures: list[dict] = field(
        default_factory=list
    )  # [{name, passed, confidence, details}]
    confidence_breakdown: dict = field(
        default_factory=dict
    )  # {final, extraction, coverage, reason}

    # ResolutionDecision context
    planner_reason: str = ""

    # ExecutionHistory summary
    execution_summary: list[dict] = field(
        default_factory=list
    )  # [{strategy, outcome, timestamp, confidence_before}]

    @classmethod
    def build(cls, state: dict) -> "ReviewerPayload":
        """Construct a ReviewerPayload from the current GraphState."""
        truth_report = state.get("truth_report")
        resolution_decision = state.get("resolution_decision")
        execution_history = state.get("execution_history") or []

        if truth_report is not None:
            missing = truth_report.field_validation.required_fields_missing
            additional = truth_report.field_validation.additional_fields
            verifier_failures = [
                {
                    "verifier_name": r.verifier_name,
                    "passed": r.passed,
                    "confidence": r.confidence,
                    "details": r.details,
                }
                for r in truth_report.verification_reports
                if r.passed is False
            ]
            confidence_breakdown = {
                "final_confidence": truth_report.final_confidence,
                "extraction_confidence": truth_report.extraction.overall_confidence,
                "coverage_score": truth_report.field_validation.coverage_score,
                "decision_reason": truth_report.decision_reason,
            }
        else:
            missing = []
            additional = []
            verifier_failures = []
            confidence_breakdown = {}

        planner_reason = (
            resolution_decision.reason if resolution_decision is not None else "no_decision"
        )

        execution_summary = [
            {
                "strategy": record.strategy.value,
                "outcome": record.outcome,
                "timestamp": record.timestamp,
                "confidence_before": record.confidence_before,
            }
            for record in execution_history
        ]

        return cls(
            document_id=state["document_id"],
            doc_type=state.get("doc_type"),
            extracted_fields=state.get("extracted_fields") or {},
            missing_required_fields=missing,
            additional_fields=additional,
            verifier_failures=verifier_failures,
            confidence_breakdown=confidence_breakdown,
            planner_reason=planner_reason,
            execution_summary=execution_summary,
        )

    def to_interrupt_payload(self) -> dict:
        """Serialise to the dict passed to langgraph.types.interrupt()."""
        return {
            "document_id": self.document_id,
            "doc_type": self.doc_type,
            "extracted_fields": self.extracted_fields,
            "missing_required_fields": self.missing_required_fields,
            "additional_fields": self.additional_fields,
            "verifier_failures": self.verifier_failures,
            "confidence_breakdown": self.confidence_breakdown,
            "planner_reason": self.planner_reason,
            "execution_summary": self.execution_summary,
        }
