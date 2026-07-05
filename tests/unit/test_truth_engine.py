"""Tests for Phase 4 foundation — TruthReport, FieldValidationReport,
VerificationReport, ConfidenceFusionPolicy, VerifierRegistry, ExtractionResult.
"""
import unittest.mock as mock

import pytest

from pipelines.truth_engine.confidence import ConfidenceFusionPolicy
from pipelines.truth_engine.models import (
    ExtractionResult,
    FieldValidationReport,
    TruthReport,
    VerificationReport,
)
from pipelines.truth_engine.verifier_registry import (
    MAX_TOOL_CALLS,
    VerifierRegistry,
    VerifierSpec,
    verifier_registry,
)


# ---------------------------------------------------------------------------
# ExtractionResult
# ---------------------------------------------------------------------------


def test_extraction_result_construction() -> None:
    r = ExtractionResult(
        fields={"surname": "DOE", "passport_number": "P123"},
        overall_confidence=0.92,
        context_used=True,
        sample_count=1,
    )
    assert r.fields["surname"] == "DOE"
    assert r.overall_confidence == pytest.approx(0.92)
    assert r.context_used is True
    assert r.sample_count == 1
    assert r.retrieval_metadata is None
    assert r.success is True
    assert r.error is None


def test_extraction_result_failure_state() -> None:
    r = ExtractionResult(
        fields={},
        overall_confidence=0.0,
        context_used=False,
        sample_count=1,
        success=False,
        error="parse_error: invalid json",
    )
    assert r.success is False
    assert r.error == "parse_error: invalid json"
    assert r.fields == {}


def test_extraction_result_has_no_verification_fields() -> None:
    """Phase 3 output must not carry verification data — that belongs to Phase 4."""
    r = ExtractionResult(fields={}, overall_confidence=0.9, context_used=False, sample_count=1)
    assert not hasattr(r, "verification_passed")
    assert not hasattr(r, "tool_calls_made")


def test_extraction_result_carries_retrieval_metadata() -> None:
    meta = {"retrieved_count": 3, "doc_ids": ["d1", "d2"]}
    r = ExtractionResult(
        fields={"x": 1},
        overall_confidence=0.9,
        context_used=True,
        sample_count=1,
        retrieval_metadata=meta,
    )
    assert r.retrieval_metadata == meta


def test_extraction_result_sample_count_reflects_self_consistency_passes(
    sample_pdf_bytes,
) -> None:
    """sample_count=3 when borderline confidence triggers self-consistency voting."""
    from agents.extract_agent import extract

    borderline_response = (
        '{"fields": {"surname": "BORDER"}, "overall_confidence": 0.72}'
    )
    mock_resp = mock.MagicMock()
    mock_resp.text = borderline_response
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_resp
        result = extract(sample_pdf_bytes, "application/pdf", "passport")

    assert result.sample_count == 3


# ---------------------------------------------------------------------------
# FieldValidationReport
# ---------------------------------------------------------------------------

_PASSPORT_REQUIRED = {
    "surname": "SMITH",
    "given_names": "JOHN",
    "nationality": "GBR",
    "date_of_birth": "1990-01-01",
    "sex": "M",
    "date_of_issue": "2020-01-01",
    "date_of_expiry": "2030-01-01",
    "passport_number": "P1234567",
}


def test_field_validation_all_required_present() -> None:
    report = FieldValidationReport.build(_PASSPORT_REQUIRED, "passport")
    assert report.coverage_score == pytest.approx(1.0)
    assert report.required_fields_missing == []
    assert set(report.required_fields_present) == set(_PASSPORT_REQUIRED.keys())


def test_field_validation_partial_coverage() -> None:
    report = FieldValidationReport.build({"surname": "SMITH"}, "passport")
    assert report.coverage_score < 1.0
    assert "surname" in report.required_fields_present
    assert "given_names" in report.required_fields_missing
    assert "passport_number" in report.required_fields_missing


def test_field_validation_coverage_score_formula() -> None:
    # 1 of 8 required fields present → 0.125
    report = FieldValidationReport.build({"surname": "SMITH"}, "passport")
    assert report.coverage_score == pytest.approx(1 / 8)


def test_field_validation_additional_fields_detected() -> None:
    extracted = {**_PASSPORT_REQUIRED, "biometric_chip": True, "issue_authority": "Home Office"}
    report = FieldValidationReport.build(extracted, "passport")
    assert "biometric_chip" in report.additional_fields
    assert "issue_authority" in report.additional_fields
    assert report.coverage_score == pytest.approx(1.0)


def test_field_validation_optional_fields_not_in_additional() -> None:
    # place_of_birth is optional in passport schema — should NOT appear in additional_fields
    extracted = {**_PASSPORT_REQUIRED, "place_of_birth": "London"}
    report = FieldValidationReport.build(extracted, "passport")
    assert "place_of_birth" not in report.additional_fields


def test_field_validation_unknown_doc_type_coverage_is_one() -> None:
    report = FieldValidationReport.build({"any_field": "value"}, "nonexistent_type")
    assert report.coverage_score == pytest.approx(1.0)
    assert report.required_fields_missing == []
    assert report.required_fields_present == []


# ---------------------------------------------------------------------------
# VerificationReport
# ---------------------------------------------------------------------------


def test_verification_report_passed() -> None:
    r = VerificationReport(verifier_name="mrz_checksum", passed=True, confidence=1.0)
    assert r.passed is True
    assert r.confidence == pytest.approx(1.0)
    assert r.details == {}


def test_verification_report_failed_with_details() -> None:
    r = VerificationReport(
        verifier_name="mrz_checksum",
        passed=False,
        confidence=0.0,
        details={"expected": 4, "got": 9},
    )
    assert r.passed is False
    assert r.details["expected"] == 4


def test_verification_report_not_attempted() -> None:
    r = VerificationReport(verifier_name="balance_arithmetic", passed=None, confidence=0.0)
    assert r.passed is None


# ---------------------------------------------------------------------------
# TruthReport
# ---------------------------------------------------------------------------


def test_truth_report_construction() -> None:
    extraction = ExtractionResult(
        fields=_PASSPORT_REQUIRED,
        overall_confidence=0.92,
        context_used=True,
        sample_count=1,
    )
    fvr = FieldValidationReport.build(_PASSPORT_REQUIRED, "passport")
    vr = VerificationReport(verifier_name="mrz_checksum", passed=True, confidence=1.0)
    report = TruthReport(
        extraction=extraction,
        field_validation=fvr,
        verification_reports=[vr],
        final_confidence=0.94,
        decision_reason="classify=0.95 extraction=0.92 coverage=1.00",
    )
    assert report.extraction is extraction
    assert report.field_validation is fvr
    assert len(report.verification_reports) == 1
    assert report.final_confidence == pytest.approx(0.94)
    assert "classify" in report.decision_reason


def test_truth_report_is_evidence_only() -> None:
    """TruthReport carries no routing decision — only confidence evidence."""
    extraction = ExtractionResult(
        fields={}, overall_confidence=0.5, context_used=False, sample_count=1
    )
    fvr = FieldValidationReport.build({}, "passport")
    report = TruthReport(
        extraction=extraction,
        field_validation=fvr,
        verification_reports=[],
        final_confidence=0.45,
        decision_reason="low_coverage",
    )
    assert not hasattr(report, "action")
    assert not hasattr(report, "routing_decision")


# ---------------------------------------------------------------------------
# ConfidenceFusionPolicy
# ---------------------------------------------------------------------------


def test_fusion_basic_weighted_average() -> None:
    policy = ConfidenceFusionPolicy()
    final, reason = policy.fuse(
        classify_confidence=0.9,
        extraction_confidence=0.85,
        coverage_score=1.0,
        verification_reports=[],
    )
    expected = 0.20 * 0.9 + 0.50 * 0.85 + 0.30 * 1.0
    assert final == pytest.approx(expected, abs=1e-3)
    assert "classify=0.90" in reason
    assert "extraction=0.85" in reason


def test_fusion_verification_failure_caps_confidence() -> None:
    policy = ConfidenceFusionPolicy()
    vr = VerificationReport(verifier_name="mrz_checksum", passed=False, confidence=0.0)
    final, reason = policy.fuse(0.95, 0.90, 1.0, [vr])
    assert final <= policy.verification_failure_cap
    assert "mrz_checksum" in reason
    assert "capped_by_failures" in reason


def test_fusion_verification_pass_adds_bonus() -> None:
    policy = ConfidenceFusionPolicy()
    vr = VerificationReport(verifier_name="mrz_checksum", passed=True, confidence=1.0)
    without_vr, _ = policy.fuse(0.8, 0.75, 0.9, [])
    with_vr, _ = policy.fuse(0.8, 0.75, 0.9, [vr])
    assert with_vr > without_vr
    assert with_vr == pytest.approx(without_vr + policy.verification_pass_bonus, abs=1e-4)


def test_fusion_clamps_output_to_unit_interval() -> None:
    policy = ConfidenceFusionPolicy()
    high, _ = policy.fuse(1.0, 1.0, 1.0, [])
    assert 0.0 <= high <= 1.0
    low, _ = policy.fuse(0.0, 0.0, 0.0, [])
    assert 0.0 <= low <= 1.0


def test_fusion_no_verification_skips_modifiers() -> None:
    policy = ConfidenceFusionPolicy()
    final, reason = policy.fuse(0.8, 0.7, 0.8, [])
    assert "capped_by_failures" not in reason
    assert "bonus_for_passes" not in reason


def test_fusion_not_attempted_verifications_are_neutral() -> None:
    """passed=None (not attempted) should have no effect on the score."""
    policy = ConfidenceFusionPolicy()
    vr_none = VerificationReport(verifier_name="mrz_checksum", passed=None, confidence=0.0)
    without, _ = policy.fuse(0.85, 0.80, 0.95, [])
    with_none, _ = policy.fuse(0.85, 0.80, 0.95, [vr_none])
    assert without == pytest.approx(with_none, abs=1e-4)


def test_fusion_decision_reason_is_non_empty() -> None:
    policy = ConfidenceFusionPolicy()
    _, reason = policy.fuse(0.9, 0.85, 1.0, [])
    assert len(reason) > 0
    assert "final=" in reason


# ---------------------------------------------------------------------------
# VerifierRegistry
# ---------------------------------------------------------------------------


def test_verifier_registry_passport_has_mrz_checksum() -> None:
    specs = verifier_registry.get("passport")
    assert len(specs) >= 1
    names = [s.name for s in specs]
    assert "mrz_checksum" in names


def test_verifier_registry_bank_statement_has_balance_arithmetic() -> None:
    specs = verifier_registry.get("bank_statement")
    assert len(specs) >= 1
    names = [s.name for s in specs]
    assert "balance_arithmetic" in names


def test_verifier_registry_gst_invoice_has_no_verifiers() -> None:
    # Placeholder — verifier not yet implemented
    assert verifier_registry.get("gst_invoice") == []


def test_verifier_registry_unregistered_type_returns_empty() -> None:
    assert verifier_registry.get("nonexistent_type") == []


def test_verifier_registry_has_verifiers_flag() -> None:
    assert verifier_registry.has_verifiers("passport") is True
    assert verifier_registry.has_verifiers("bank_statement") is True
    assert verifier_registry.has_verifiers("gst_invoice") is False


def test_verifier_registry_spec_fn_is_callable() -> None:
    for spec in verifier_registry.get("passport"):
        assert callable(spec.fn)


def test_verifier_registry_passport_mrz_fn_produces_valid_result() -> None:
    specs = verifier_registry.get("passport")
    mrz_spec = next(s for s in specs if s.name == "mrz_checksum")
    result = mrz_spec.fn(mrz_string="SMITH", check_digit=4)
    assert "valid" in result


def test_verifier_registry_add_custom_verifier() -> None:
    def dummy_verifier(**kwargs) -> dict:
        return {"valid": True}

    reg = VerifierRegistry()
    reg.register("test_doc", VerifierSpec(name="dummy", fn=dummy_verifier))
    specs = reg.get("test_doc")
    assert len(specs) == 1
    assert specs[0].name == "dummy"
    assert specs[0].fn() == {"valid": True}


def test_max_tool_calls_constant_is_positive() -> None:
    assert MAX_TOOL_CALLS > 0


# ---------------------------------------------------------------------------
# Migration: verification no longer in Phase 3
# ---------------------------------------------------------------------------


def test_extract_result_has_no_verification_state(sample_pdf_bytes) -> None:
    """Regression: extract() must not set verification_passed or tool_calls_made."""
    from agents.extract_agent import extract

    mock_resp = mock.MagicMock()
    mock_resp.text = (
        '{"fields": {"surname": "SMITH", "passport_number": "P1"}, "overall_confidence": 0.9}'
    )
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_resp
        result = extract(sample_pdf_bytes, "application/pdf", "passport")

    assert not hasattr(result, "verification_passed")
    assert not hasattr(result, "tool_calls_made")
    assert isinstance(result, ExtractionResult)


def test_extract_agent_does_not_import_verifiers() -> None:
    """Regression: agents.extract_agent must not import or call verifier functions."""
    import agents.extract_agent as agent_module

    assert not hasattr(agent_module, "mrz_checksum")
    assert not hasattr(agent_module, "balance_arithmetic")
    assert not hasattr(agent_module, "_VERIFIABLE")
    assert not hasattr(agent_module, "generate_with_tools")
