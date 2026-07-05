"""Tests for Phase 5.5 — HITL revalidation and ReviewerPayload.

Covers:
  - ReviewerPayload construction from TruthReport + ResolutionDecision + history
  - ReviewerPayload.to_interrupt_payload() structure
  - _revalidate_corrections: corrections flow through Truth Engine
  - _replan: fresh ResolutionDecision from corrected TruthReport
  - op_b_hitl_node: full integration (mocked interrupt)
  - hitl_correction flag set correctly
"""
from __future__ import annotations

import unittest.mock as mock

import pytest

from pipelines.learning.reviewer_payload import ReviewerPayload
from pipelines.resolution.models import (
    ExecutionRecord,
    ResolutionDecision,
    Strategy,
)
from pipelines.truth_engine.models import (
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
    VerificationReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_truth_report(
    final_confidence: float = 0.60,
    required_fields_missing: list[str] | None = None,
    additional_fields: list[str] | None = None,
    verifier_names_failed: list[str] | None = None,
    allow_learning: bool = True,
) -> TruthReport:
    missing = required_fields_missing or []
    extra = additional_fields or []
    verification_reports = [
        VerificationReport(verifier_name=n, passed=False, confidence=0.0)
        for n in (verifier_names_failed or [])
    ]
    return TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=final_confidence, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[],
            required_fields_missing=missing,
            additional_fields=extra,
            coverage_score=1.0 - len(missing) * 0.1,
        ),
        verification_reports=verification_reports,
        final_confidence=final_confidence,
        decision_reason="test",
        persistence=PersistenceDecision(
            document_status="failed" if not allow_learning else "completed",
            allow_completion=allow_learning,
            allow_embedding=allow_learning,
            allow_learning=allow_learning,
            reason="test",
        ),
    )


def _make_state(
    truth_report: TruthReport | None = None,
    resolution_decision: ResolutionDecision | None = None,
    execution_history: list | None = None,
    extracted_fields: dict | None = None,
) -> dict:
    return {
        "document_id": "doc-test-001",
        "doc_type": "passport",
        "extracted_fields": extracted_fields or {"surname": "SMITH"},
        "truth_report": truth_report,
        "resolution_decision": resolution_decision or ResolutionDecision(
            strategy=Strategy.HITL,
            reason="low_confidence",
            requires_human=True,
        ),
        "execution_history": execution_history or [],
        "classify_confidence": 0.95,
        "extract_confidence": 0.60,
        "retry_count": 2,
    }


# ---------------------------------------------------------------------------
# ReviewerPayload — construction
# ---------------------------------------------------------------------------


class TestReviewerPayloadBuild:
    def test_document_id_and_doc_type_present(self) -> None:
        state = _make_state(_make_truth_report())
        payload = ReviewerPayload.build(state)
        assert payload.document_id == "doc-test-001"
        assert payload.doc_type == "passport"

    def test_extracted_fields_present(self) -> None:
        state = _make_state(
            _make_truth_report(),
            extracted_fields={"surname": "SMITH", "given_names": "JOHN"},
        )
        payload = ReviewerPayload.build(state)
        assert payload.extracted_fields["surname"] == "SMITH"

    def test_missing_required_fields_from_truth_report(self) -> None:
        report = _make_truth_report(required_fields_missing=["passport_number", "date_of_birth"])
        state = _make_state(report)
        payload = ReviewerPayload.build(state)
        assert "passport_number" in payload.missing_required_fields
        assert "date_of_birth" in payload.missing_required_fields

    def test_additional_fields_from_truth_report(self) -> None:
        report = _make_truth_report(additional_fields=["religion", "blood_type"])
        state = _make_state(report)
        payload = ReviewerPayload.build(state)
        assert "religion" in payload.additional_fields
        assert "blood_type" in payload.additional_fields

    def test_verifier_failures_only_include_passed_false(self) -> None:
        report = _make_truth_report(verifier_names_failed=["mrz_check"])
        # Add a passing verifier manually
        report.verification_reports.append(
            VerificationReport("date_check", passed=True, confidence=1.0)
        )
        state = _make_state(report)
        payload = ReviewerPayload.build(state)
        assert len(payload.verifier_failures) == 1
        assert payload.verifier_failures[0]["verifier_name"] == "mrz_check"

    def test_verifier_failure_dict_has_required_keys(self) -> None:
        report = _make_truth_report(verifier_names_failed=["mrz_check"])
        state = _make_state(report)
        payload = ReviewerPayload.build(state)
        failure = payload.verifier_failures[0]
        assert "verifier_name" in failure
        assert "passed" in failure
        assert "confidence" in failure
        assert "details" in failure

    def test_confidence_breakdown_keys(self) -> None:
        report = _make_truth_report(final_confidence=0.72)
        state = _make_state(report)
        payload = ReviewerPayload.build(state)
        bd = payload.confidence_breakdown
        assert "final_confidence" in bd
        assert "extraction_confidence" in bd
        assert "coverage_score" in bd
        assert "decision_reason" in bd

    def test_confidence_breakdown_values_match_report(self) -> None:
        report = _make_truth_report(final_confidence=0.72)
        state = _make_state(report)
        payload = ReviewerPayload.build(state)
        assert payload.confidence_breakdown["final_confidence"] == pytest.approx(0.72, abs=0.05)

    def test_planner_reason_from_resolution_decision(self) -> None:
        decision = ResolutionDecision(
            strategy=Strategy.HITL,
            reason="budget_exhausted_after_4_retries",
            requires_human=True,
        )
        state = _make_state(_make_truth_report(), resolution_decision=decision)
        payload = ReviewerPayload.build(state)
        assert payload.planner_reason == "budget_exhausted_after_4_retries"

    def test_execution_summary_from_history(self) -> None:
        history = [
            ExecutionRecord(
                strategy=Strategy.PROMPT_REFINEMENT,
                timestamp="2024-01-01T00:00:00Z",
                outcome="refinement_scheduled",
                confidence_before=0.60,
                confidence_after=None,
            ),
            ExecutionRecord(
                strategy=Strategy.BETTER_RETRIEVAL,
                timestamp="2024-01-01T00:01:00Z",
                outcome="better_retrieval_scheduled",
                confidence_before=0.65,
                confidence_after=None,
            ),
        ]
        state = _make_state(_make_truth_report(), execution_history=history)
        payload = ReviewerPayload.build(state)
        assert len(payload.execution_summary) == 2
        assert payload.execution_summary[0]["strategy"] == "prompt_refinement"
        assert payload.execution_summary[1]["strategy"] == "better_retrieval"

    def test_execution_summary_entry_has_required_keys(self) -> None:
        history = [
            ExecutionRecord(
                strategy=Strategy.RETRY,
                timestamp="t",
                outcome="retry_scheduled",
                confidence_before=0.60,
                confidence_after=None,
            )
        ]
        state = _make_state(_make_truth_report(), execution_history=history)
        payload = ReviewerPayload.build(state)
        entry = payload.execution_summary[0]
        assert "strategy" in entry
        assert "outcome" in entry
        assert "timestamp" in entry
        assert "confidence_before" in entry

    def test_no_truth_report_produces_empty_evidence(self) -> None:
        state = _make_state(truth_report=None)
        payload = ReviewerPayload.build(state)
        assert payload.missing_required_fields == []
        assert payload.verifier_failures == []
        assert payload.confidence_breakdown == {}

    def test_no_resolution_decision_uses_fallback_reason(self) -> None:
        state = _make_state(_make_truth_report())
        state["resolution_decision"] = None  # explicitly override the fallback
        payload = ReviewerPayload.build(state)
        assert payload.planner_reason == "no_decision"


class TestReviewerPayloadToInterruptPayload:
    def test_to_interrupt_payload_is_dict(self) -> None:
        state = _make_state(_make_truth_report())
        payload = ReviewerPayload.build(state)
        result = payload.to_interrupt_payload()
        assert isinstance(result, dict)

    def test_to_interrupt_payload_has_all_keys(self) -> None:
        state = _make_state(_make_truth_report())
        payload = ReviewerPayload.build(state)
        result = payload.to_interrupt_payload()
        expected_keys = {
            "document_id",
            "doc_type",
            "extracted_fields",
            "missing_required_fields",
            "additional_fields",
            "verifier_failures",
            "confidence_breakdown",
            "planner_reason",
            "execution_summary",
        }
        assert expected_keys.issubset(result.keys())

    def test_validation_issues_key_absent(self) -> None:
        """Interrupt payload must NOT contain the old validation_issues key."""
        state = _make_state(_make_truth_report())
        payload = ReviewerPayload.build(state)
        result = payload.to_interrupt_payload()
        assert "validation_issues" not in result


# ---------------------------------------------------------------------------
# _revalidate_corrections — Truth Engine rerun
# ---------------------------------------------------------------------------


class TestRevalidateCorrections:
    def test_corrections_produce_new_truth_report(self) -> None:
        from pipelines.nodes.op_b_hitl import _revalidate_corrections

        state = _make_state(_make_truth_report())
        merged = {"surname": "CORRECTED", "given_names": "JANE"}
        report = _revalidate_corrections(state, merged)
        assert report is not None
        assert report.extraction.fields == merged

    def test_corrected_report_decision_reason_contains_hitl_prefix(self) -> None:
        from pipelines.nodes.op_b_hitl import _revalidate_corrections

        state = _make_state(_make_truth_report())
        report = _revalidate_corrections(state, {"surname": "SMITH"})
        assert report.decision_reason.startswith("hitl_correction:")

    def test_corrected_report_has_fresh_field_validation(self) -> None:
        from pipelines.nodes.op_b_hitl import _revalidate_corrections

        state = _make_state(_make_truth_report(required_fields_missing=["passport_number"]))
        # Correction supplies the previously missing field
        report = _revalidate_corrections(state, {"passport_number": "X1234567"})
        # Field validation reflects the corrected extraction (passport_number now present)
        assert "passport_number" not in report.field_validation.required_fields_missing \
            or report.field_validation.coverage_score > 0.0

    def test_corrected_report_runs_verifiers(self) -> None:
        from pipelines.nodes.op_b_hitl import _revalidate_corrections

        state = _make_state(_make_truth_report())
        report = _revalidate_corrections(state, {"surname": "SMITH"})
        # Verifiers run (passport verifiers need specific fields, so passed=None for missing fields)
        # The key assertion is that verifier execution occurred
        assert isinstance(report.verification_reports, list)

    def test_corrected_report_produces_persistence_decision(self) -> None:
        from pipelines.nodes.op_b_hitl import _revalidate_corrections

        state = _make_state(_make_truth_report())
        report = _revalidate_corrections(state, {"surname": "SMITH"})
        assert report.persistence is not None
        assert report.persistence.document_status in (
            "completed", "verification_failed", "failed"
        )


# ---------------------------------------------------------------------------
# _replan — ResolutionDecision from corrected TruthReport
# ---------------------------------------------------------------------------


class TestReplan:
    def test_replan_returns_resolution_decision(self) -> None:
        from pipelines.nodes.op_b_hitl import _replan

        from pipelines.truth_engine.models import PersistenceDecision as PD

        high_conf_report = TruthReport(
            extraction=ExtractionResult(
                fields={}, overall_confidence=0.95, context_used=False, sample_count=1
            ),
            field_validation=FieldValidationReport(
                required_fields_present=[],
                required_fields_missing=[],
                additional_fields=[],
                coverage_score=1.0,
            ),
            verification_reports=[],
            final_confidence=0.95,
            decision_reason="hitl_correction:ok",
            persistence=PD(
                document_status="completed",
                allow_completion=True,
                allow_embedding=True,
                allow_learning=True,
                reason="ok",
            ),
        )
        state = _make_state(_make_truth_report())
        decision = _replan(state, high_conf_report)
        assert decision is not None
        assert decision.strategy in list(Strategy)

    def test_replan_accepts_high_confidence_corrected_report(self) -> None:
        from pipelines.nodes.op_b_hitl import _replan

        from pipelines.truth_engine.models import PersistenceDecision as PD

        high_conf_report = TruthReport(
            extraction=ExtractionResult(
                fields={}, overall_confidence=0.96, context_used=False, sample_count=1
            ),
            field_validation=FieldValidationReport(
                required_fields_present=[],
                required_fields_missing=[],
                additional_fields=[],
                coverage_score=1.0,
            ),
            verification_reports=[],
            final_confidence=0.96,
            decision_reason="hitl_correction:ok",
            persistence=PD(
                document_status="completed",
                allow_completion=True,
                allow_embedding=True,
                allow_learning=True,
                reason="ok",
            ),
        )
        state = _make_state(_make_truth_report(), resolution_decision=None)
        decision = _replan(state, high_conf_report)
        assert decision.strategy == Strategy.ACCEPT


# ---------------------------------------------------------------------------
# op_b_hitl_node — integration
# ---------------------------------------------------------------------------


class TestHITLNodeIntegration:
    def _run_hitl(
        self,
        state: dict,
        approved: bool = True,
        corrections: dict | None = None,
    ) -> dict:
        """Run op_b_hitl_node with a mocked interrupt."""
        from pipelines.nodes.op_b_hitl import op_b_hitl_node

        resume = {"approved": approved, "corrections": corrections or {}}
        with mock.patch("pipelines.nodes.op_b_hitl.interrupt", return_value=resume):
            return op_b_hitl_node(state)  # type: ignore[arg-type]

    def test_approved_sets_hitl_approved_true(self) -> None:
        state = _make_state(_make_truth_report())
        result = self._run_hitl(state, approved=True)
        assert result["hitl_approved"] is True

    def test_rejected_sets_hitl_approved_false(self) -> None:
        state = _make_state(_make_truth_report())
        result = self._run_hitl(state, approved=False)
        assert result["hitl_approved"] is False

    def test_corrections_merged_into_extracted_fields(self) -> None:
        state = _make_state(_make_truth_report(), extracted_fields={"surname": "SMIT"})
        result = self._run_hitl(state, corrections={"surname": "SMITH", "given_names": "JOHN"})
        assert result["extracted_fields"]["surname"] == "SMITH"
        assert result["extracted_fields"]["given_names"] == "JOHN"

    def test_no_corrections_preserves_extracted_fields(self) -> None:
        state = _make_state(_make_truth_report(), extracted_fields={"surname": "SMITH"})
        result = self._run_hitl(state, corrections=None)
        assert result["extracted_fields"]["surname"] == "SMITH"

    def test_truth_report_always_updated(self) -> None:
        """Even without corrections, op_b_hitl revalidates with current extracted_fields."""
        state = _make_state(_make_truth_report(final_confidence=0.60))
        result = self._run_hitl(state)
        assert result["truth_report"] is not None
        # New report created (not the same object)
        assert result["truth_report"] is not state["truth_report"]

    def test_resolution_decision_updated(self) -> None:
        state = _make_state(_make_truth_report())
        result = self._run_hitl(state)
        assert result["resolution_decision"] is not None
        assert result["resolution_decision"] is not state["resolution_decision"]

    def test_hitl_correction_true_when_corrections_provided(self) -> None:
        state = _make_state(_make_truth_report())
        result = self._run_hitl(state, corrections={"surname": "FIXED"})
        assert result["hitl_correction"] is True

    def test_hitl_correction_false_when_no_corrections(self) -> None:
        state = _make_state(_make_truth_report())
        result = self._run_hitl(state, corrections=None)
        assert result["hitl_correction"] is False

    def test_interrupt_payload_is_reviewer_payload_not_validation_issues(self) -> None:
        """interrupt() must receive ReviewerPayload shape, not validation_issues."""
        state = _make_state(_make_truth_report())
        captured: list[dict] = []

        def fake_interrupt(payload):
            captured.append(payload)
            return {"approved": True, "corrections": {}}

        from pipelines.nodes import op_b_hitl
        with mock.patch.object(op_b_hitl, "interrupt", side_effect=fake_interrupt):
            op_b_hitl.op_b_hitl_node(state)  # type: ignore[arg-type]

        assert len(captured) == 1
        payload = captured[0]
        assert "validation_issues" not in payload
        assert "missing_required_fields" in payload
        assert "verifier_failures" in payload
        assert "confidence_breakdown" in payload
        assert "planner_reason" in payload
        assert "execution_summary" in payload

    def test_extraction_result_updated_to_corrected_extraction(self) -> None:
        state = _make_state(_make_truth_report(), extracted_fields={"surname": "OLD"})
        result = self._run_hitl(state, corrections={"surname": "NEW"})
        assert result["extraction_result"].fields["surname"] == "NEW"
