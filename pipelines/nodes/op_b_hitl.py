"""HITL node — human review with full TruthReport revalidation.

Phase 5.5 changes:
  - Interrupt payload is a structured ReviewerPayload (not bare validation_issues)
  - After corrections are received, Truth Engine is re-run on the merged fields
    so that allow_learning / allow_embedding reflect the corrected evidence
  - ResolutionDecision is replanned from the corrected TruthReport
  - Routing is unchanged: route_after_hitl still reads hitl_approved

Human corrections MUST flow through the Truth Engine.  No correction may bypass
evidence evaluation and be persisted or learned from without revalidation.
"""

from __future__ import annotations

import logging

from langgraph.types import interrupt

from config.settings import settings
from pipelines.learning.reviewer_payload import ReviewerPayload
from pipelines.resolution.models import PlannerBundle, ResolutionDecision
from pipelines.resolution.planner import ResolutionPlanner
from pipelines.state import GraphState
from pipelines.truth_engine.confidence import ConfidenceFusionPolicy
from pipelines.truth_engine.models import (
    EvidenceBundle,
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
    VerificationReport,
)
from pipelines.truth_engine.verifier_registry import VERIFIER_VERSION, verifier_registry

log = logging.getLogger(__name__)

_fusion_policy = ConfidenceFusionPolicy()
_planner = ResolutionPlanner()


def _revalidate_corrections(state: GraphState, merged_fields: dict) -> TruthReport:
    """Run Truth Engine logic on human-corrected fields.

    Called inline (not via the node) so we stay inside the HITL interrupt
    cycle without adding new graph edges.  Produces a fresh TruthReport whose
    persistence flags (allow_learning, allow_embedding) reflect the corrected
    evidence rather than the pre-HITL extraction.
    """
    doc_type = state.get("doc_type") or ""
    classify_confidence = state.get("classify_confidence") or 0.0

    prior = state.get("extraction_result")
    context_used = prior.context_used if prior is not None else False

    extraction = ExtractionResult(
        fields=merged_fields,
        overall_confidence=state.get("extract_confidence") or 0.0,
        context_used=context_used,
        sample_count=1,
    )

    field_validation = FieldValidationReport.build(merged_fields, doc_type)

    verification_reports: list[VerificationReport] = []
    for spec in verifier_registry.get(doc_type):
        kwargs = spec.extractor(merged_fields)
        if kwargs is None:
            report = VerificationReport(
                verifier_name=spec.name,
                passed=None,
                confidence=0.0,
                details={"reason": "required_fields_not_present"},
            )
        else:
            try:
                result = spec.fn(**kwargs)
                passed = bool(result.get("valid", False))
                report = VerificationReport(
                    verifier_name=spec.name,
                    passed=passed,
                    confidence=1.0 if passed else 0.0,
                    details=result,
                )
            except Exception as exc:
                report = VerificationReport(
                    verifier_name=spec.name,
                    passed=None,
                    confidence=0.0,
                    details={"error": str(exc)},
                )
        verification_reports.append(report)
        log.debug(
            "event=HITLVerifier verifier=%s passed=%s",
            report.verifier_name,
            report.passed,
        )

    bundle = EvidenceBundle(
        classify_confidence=classify_confidence,
        extraction_confidence=extraction.overall_confidence,
        coverage_score=field_validation.coverage_score,
        verification_reports=verification_reports,
    )
    final_confidence, decision_reason = _fusion_policy.fuse(bundle)

    persistence = PersistenceDecision.from_truth(
        verification_reports=verification_reports,
        final_confidence=final_confidence,
        threshold=settings.CONFIDENCE_THRESHOLD,
    )

    return TruthReport(
        extraction=extraction,
        field_validation=field_validation,
        verification_reports=verification_reports,
        final_confidence=final_confidence,
        decision_reason=f"hitl_correction:{decision_reason}",
        persistence=persistence,
        verifier_version=VERIFIER_VERSION,
    )


def _replan(state: GraphState, truth_report: TruthReport) -> ResolutionDecision:
    """Run the planner on a fresh TruthReport from corrected fields."""
    history = list(state.get("execution_history") or [])
    retry_count = state.get("retry_count", 0) or 0
    bundle = PlannerBundle(
        truth_report=truth_report,
        execution_history=history,
        retry_count=retry_count,
        remaining_budget=max(0, _planner._max_retries - retry_count),
    )
    return _planner.plan(bundle)


def op_b_hitl_node(state: GraphState) -> dict:
    """Pause the graph and surface a TruthReport summary for human review.

    Interrupt payload: ReviewerPayload.to_interrupt_payload()
      - missing_required_fields, additional_fields, verifier_failures
      - confidence_breakdown, planner_reason, execution_summary

    Resume payload: {"approved": bool, "corrections": dict | None}

    After corrections:
      1. Merge corrections into extracted_fields
      2. Re-run Truth Engine on merged fields → fresh TruthReport
      3. Re-run planner → fresh ResolutionDecision
      4. Return updated truth_report, resolution_decision, hitl_correction=True

    Routing unchanged: route_after_hitl(state) reads hitl_approved.
    """
    payload = ReviewerPayload.build(state)  # type: ignore[arg-type]

    decision = interrupt(payload.to_interrupt_payload())

    approved = bool(decision.get("approved"))
    corrections = decision.get("corrections") or {}
    merged_fields = {**(state.get("extracted_fields") or {}), **corrections}
    is_correction = bool(corrections)

    new_truth_report = _revalidate_corrections(state, merged_fields)
    new_decision = _replan(state, new_truth_report)

    log.info(
        "event=HITLRevalidated approved=%s has_corrections=%s "
        "new_confidence=%.4f new_strategy=%s allow_learning=%s",
        approved,
        is_correction,
        new_truth_report.final_confidence,
        new_decision.strategy.value,
        new_truth_report.persistence.allow_learning,
    )

    return {
        "hitl_required": True,
        "hitl_approved": approved,
        "extracted_fields": merged_fields,
        "extraction_result": new_truth_report.extraction,
        "truth_report": new_truth_report,
        "resolution_decision": new_decision,
        "hitl_correction": is_correction,
    }
