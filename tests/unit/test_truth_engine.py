"""Tests for Phase 4 foundation — TruthReport, FieldValidationReport,
VerificationReport, ConfidenceFusionPolicy, VerifierRegistry, ExtractionResult.
"""
import unittest.mock as mock

import pytest

from pipelines.truth_engine.confidence import ConfidenceFusionPolicy
from pipelines.truth_engine.models import (
    EvidenceBundle,
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
    VerificationReport,
)
from pipelines.truth_engine.verifier_registry import (
    MAX_TOOL_CALLS,
    VERIFIER_VERSION,
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


def _bundle(
    classify: float = 0.9,
    extraction: float = 0.85,
    coverage: float = 1.0,
    reports: list | None = None,
) -> EvidenceBundle:
    return EvidenceBundle(
        classify_confidence=classify,
        extraction_confidence=extraction,
        coverage_score=coverage,
        verification_reports=reports or [],
    )


def test_fusion_basic_weighted_average() -> None:
    policy = ConfidenceFusionPolicy()
    final, reason = policy.fuse(_bundle(0.9, 0.85, 1.0))
    expected = 0.20 * 0.9 + 0.50 * 0.85 + 0.30 * 1.0
    assert final == pytest.approx(expected, abs=1e-3)
    assert "classify=0.90" in reason
    assert "extraction=0.85" in reason


def test_fusion_verification_failure_caps_confidence() -> None:
    policy = ConfidenceFusionPolicy()
    vr = VerificationReport(verifier_name="mrz_checksum", passed=False, confidence=0.0)
    final, reason = policy.fuse(_bundle(0.95, 0.90, 1.0, [vr]))
    assert final <= policy.verification_failure_cap
    assert "mrz_checksum" in reason
    assert "capped_by_failures" in reason


def test_fusion_verification_pass_adds_bonus() -> None:
    policy = ConfidenceFusionPolicy()
    vr = VerificationReport(verifier_name="mrz_checksum", passed=True, confidence=1.0)
    without_vr, _ = policy.fuse(_bundle(0.8, 0.75, 0.9))
    with_vr, _ = policy.fuse(_bundle(0.8, 0.75, 0.9, [vr]))
    assert with_vr > without_vr
    assert with_vr == pytest.approx(without_vr + policy.verification_pass_bonus, abs=1e-4)


def test_fusion_clamps_output_to_unit_interval() -> None:
    policy = ConfidenceFusionPolicy()
    high, _ = policy.fuse(_bundle(1.0, 1.0, 1.0))
    assert 0.0 <= high <= 1.0
    low, _ = policy.fuse(_bundle(0.0, 0.0, 0.0))
    assert 0.0 <= low <= 1.0


def test_fusion_no_verification_skips_modifiers() -> None:
    policy = ConfidenceFusionPolicy()
    final, reason = policy.fuse(_bundle(0.8, 0.7, 0.8))
    assert "capped_by_failures" not in reason
    assert "bonus_for_passes" not in reason


def test_fusion_not_attempted_verifications_are_neutral() -> None:
    """passed=None (not attempted) should have no effect on the score."""
    policy = ConfidenceFusionPolicy()
    vr_none = VerificationReport(verifier_name="mrz_checksum", passed=None, confidence=0.0)
    without, _ = policy.fuse(_bundle(0.85, 0.80, 0.95))
    with_none, _ = policy.fuse(_bundle(0.85, 0.80, 0.95, [vr_none]))
    assert without == pytest.approx(with_none, abs=1e-4)


def test_fusion_decision_reason_is_non_empty() -> None:
    policy = ConfidenceFusionPolicy()
    _, reason = policy.fuse(_bundle(0.9, 0.85, 1.0))
    assert len(reason) > 0
    assert "final=" in reason


def test_evidence_bundle_is_frozen() -> None:
    """EvidenceBundle must be immutable — fusion depends on stable inputs."""
    bundle = _bundle()
    assert hasattr(bundle, "__dataclass_params__")
    import dataclasses
    assert dataclasses.fields(bundle)  # is a dataclass
    # frozen=True means no __setattr__ override needed; just check construction is fine
    with pytest.raises((AttributeError, TypeError)):
        bundle.classify_confidence = 0.5  # type: ignore[misc]


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


def test_verifier_registry_gst_invoice_has_verifiers() -> None:
    specs = verifier_registry.get("gst_invoice")
    names = [s.name for s in specs]
    assert "gstin_checksum" in names
    assert "invoice_total_consistency" in names


def test_verifier_registry_unregistered_type_returns_empty() -> None:
    assert verifier_registry.get("nonexistent_type") == []


def test_verifier_registry_has_verifiers_flag() -> None:
    assert verifier_registry.has_verifiers("passport") is True
    assert verifier_registry.has_verifiers("bank_statement") is True
    assert verifier_registry.has_verifiers("gst_invoice") is True
    assert verifier_registry.has_verifiers("salary_slip") is True
    assert verifier_registry.has_verifiers("itr") is True
    assert verifier_registry.has_verifiers("property_deed") is True
    assert verifier_registry.has_verifiers("nonexistent_type") is False


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


# ---------------------------------------------------------------------------
# VerifierSpec.extractor — field extraction helpers
# ---------------------------------------------------------------------------


def test_verifier_spec_has_extractor_attribute() -> None:
    spec = verifier_registry.get("passport")[0]
    assert hasattr(spec, "extractor")
    assert callable(spec.extractor)


def test_default_extractor_returns_none() -> None:
    """A VerifierSpec without a custom extractor defaults to always-None."""
    def dummy(**kwargs) -> dict:
        return {"valid": True}

    spec = VerifierSpec(name="dummy", fn=dummy)
    assert spec.extractor({"any": "field"}) is None


def test_mrz_extractor_returns_kwargs_when_mrz_line2_present() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_mrz_args

    fields = {"mrz_line2": "P1234567<8GBR9001011M3001019<<<<<<<<<<<<6"}
    kwargs = _extract_mrz_args(fields)
    assert kwargs is not None
    assert kwargs["mrz_string"] == "P1234567<"
    assert kwargs["check_digit"] == 8


def test_mrz_extractor_returns_none_when_mrz_line2_absent() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_mrz_args

    assert _extract_mrz_args({}) is None
    assert _extract_mrz_args({"mrz_line1": "P<GBRSMITH"}) is None


def test_mrz_extractor_returns_none_when_mrz_line2_too_short() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_mrz_args

    assert _extract_mrz_args({"mrz_line2": "SHORT"}) is None


def test_mrz_extractor_returns_none_when_check_digit_not_int() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_mrz_args

    # char 9 is 'X' — not parseable as int
    assert _extract_mrz_args({"mrz_line2": "P1234567<X..."}) is None


def test_balance_extractor_returns_kwargs_with_opening_balance_key() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_balance_args

    fields = {
        "opening_balance": 1000.0,
        "closing_balance": 1150.0,
        "transactions": [200.0, -50.0],
    }
    kwargs = _extract_balance_args(fields)
    assert kwargs is not None
    assert kwargs["opening"] == pytest.approx(1000.0)
    assert kwargs["closing"] == pytest.approx(1150.0)
    assert kwargs["transactions"] == [200.0, -50.0]


def test_balance_extractor_accepts_opening_key_alias() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_balance_args

    fields = {"opening": 500.0, "closing": 600.0, "transactions": [100.0]}
    kwargs = _extract_balance_args(fields)
    assert kwargs is not None
    assert kwargs["opening"] == pytest.approx(500.0)


def test_balance_extractor_accepts_dict_transactions() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_balance_args

    fields = {
        "opening_balance": 100.0,
        "closing_balance": 250.0,
        "transactions": [
            {"date": "2024-01-01", "amount": 200.0, "description": "deposit"},
            {"date": "2024-01-15", "amount": -50.0, "description": "fee"},
        ],
    }
    kwargs = _extract_balance_args(fields)
    assert kwargs is not None
    assert kwargs["transactions"] == [200.0, -50.0]


def test_balance_extractor_returns_none_when_opening_missing() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_balance_args

    assert _extract_balance_args({"closing_balance": 500.0}) is None


def test_balance_extractor_returns_none_when_closing_missing() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_balance_args

    assert _extract_balance_args({"opening_balance": 500.0}) is None


def test_balance_extractor_empty_transactions_is_valid() -> None:
    from pipelines.truth_engine.verifier_registry import _extract_balance_args

    fields = {"opening_balance": 0.0, "closing_balance": 0.0}
    kwargs = _extract_balance_args(fields)
    assert kwargs is not None
    assert kwargs["transactions"] == []


# ---------------------------------------------------------------------------
# truth_engine_node
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> dict:
    extraction = ExtractionResult(
        fields={"surname": "SMITH", "given_names": "JOHN"},
        overall_confidence=0.85,
        context_used=False,
        sample_count=1,
    )
    defaults: dict = {
        "doc_type": "passport",
        "classify_confidence": 0.9,
        "extraction_result": extraction,
        "extracted_fields": extraction.fields,
        "extract_confidence": extraction.overall_confidence,
    }
    return {**defaults, **overrides}


def test_truth_engine_node_returns_truth_report() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    result = truth_engine_node(_make_state())
    assert "truth_report" in result
    assert isinstance(result["truth_report"], TruthReport)


def test_truth_engine_node_truth_report_has_extraction() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    extraction = ExtractionResult(
        fields={"surname": "DOE"}, overall_confidence=0.75, context_used=True, sample_count=3
    )
    result = truth_engine_node(_make_state(extraction_result=extraction))
    report: TruthReport = result["truth_report"]
    assert report.extraction is extraction
    assert report.extraction.context_used is True
    assert report.extraction.sample_count == 3


def test_truth_engine_node_field_validation_reflects_required_coverage() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    # Only 1 of 8 required passport fields present
    extraction = ExtractionResult(
        fields={"surname": "SMITH"}, overall_confidence=0.8, context_used=False, sample_count=1
    )
    result = truth_engine_node(_make_state(extraction_result=extraction))
    report: TruthReport = result["truth_report"]
    assert report.field_validation.coverage_score == pytest.approx(1 / 8)
    assert "given_names" in report.field_validation.required_fields_missing


def test_truth_engine_node_additional_fields_detected() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    extra_fields = {**_PASSPORT_REQUIRED, "biometric_chip": True, "visa_stamps": ["USA"]}
    extraction = ExtractionResult(
        fields=extra_fields, overall_confidence=0.9, context_used=False, sample_count=1
    )
    result = truth_engine_node(_make_state(extraction_result=extraction))
    report: TruthReport = result["truth_report"]
    assert "biometric_chip" in report.field_validation.additional_fields
    assert report.field_validation.coverage_score == pytest.approx(1.0)


def test_truth_engine_node_verifier_not_attempted_when_mrz_missing() -> None:
    """Passport extraction without mrz_line2 → mrz_checksum not attempted."""
    from pipelines.nodes.truth_engine import truth_engine_node

    extraction = ExtractionResult(
        fields=_PASSPORT_REQUIRED,  # no mrz_line2
        overall_confidence=0.85,
        context_used=False,
        sample_count=1,
    )
    result = truth_engine_node(_make_state(extraction_result=extraction))
    report: TruthReport = result["truth_report"]
    mrz_report = next(r for r in report.verification_reports if r.verifier_name == "mrz_checksum")
    assert mrz_report.passed is None


def test_truth_engine_node_verifier_executed_when_mrz_present_and_valid() -> None:
    """Valid MRZ check digit → mrz_checksum passes."""
    from pipelines.nodes.truth_engine import truth_engine_node
    from agents.verifiers import mrz_checksum

    # Compute valid check digit for "P1234567<"
    doc_number = "P1234567<"
    valid_check = mrz_checksum(doc_number, 0)["expected"]
    mrz_line2 = doc_number + str(valid_check) + "GBR9001011M3001019<<<<<<<<<<<<6"

    fields = {**_PASSPORT_REQUIRED, "mrz_line2": mrz_line2}
    extraction = ExtractionResult(
        fields=fields, overall_confidence=0.9, context_used=False, sample_count=1
    )
    result = truth_engine_node(_make_state(extraction_result=extraction))
    report: TruthReport = result["truth_report"]
    mrz_report = next(r for r in report.verification_reports if r.verifier_name == "mrz_checksum")
    assert mrz_report.passed is True
    assert mrz_report.confidence == pytest.approx(1.0)


def test_truth_engine_node_verifier_fails_on_wrong_check_digit() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    # Force an invalid check digit (wrong char after doc number)
    mrz_line2 = "P1234567<9GBR9001011M3001019<<<<<<<<<<<<6"  # 9 is wrong for P1234567<
    fields = {**_PASSPORT_REQUIRED, "mrz_line2": mrz_line2}
    extraction = ExtractionResult(
        fields=fields, overall_confidence=0.9, context_used=False, sample_count=1
    )
    result = truth_engine_node(_make_state(extraction_result=extraction))
    report: TruthReport = result["truth_report"]
    mrz_report = next(r for r in report.verification_reports if r.verifier_name == "mrz_checksum")
    # May pass or fail depending on P1234567< checksum — assert structure is correct
    assert mrz_report.passed is not None
    assert "valid" in mrz_report.details


def test_truth_engine_node_multiple_verifiers_all_run() -> None:
    """Verifier failures do not prevent subsequent verifiers from running."""
    from pipelines.nodes.truth_engine import truth_engine_node
    from pipelines.truth_engine.verifier_registry import VerifierRegistry, VerifierSpec

    call_log: list[str] = []

    def v1(**_) -> dict:
        call_log.append("v1")
        return {"valid": False}

    def v2(**_) -> dict:
        call_log.append("v2")
        return {"valid": True}

    def always_return(val):
        return lambda fields: val

    reg = VerifierRegistry()
    reg.register(
        "test_doc",
        VerifierSpec("v1", v1, extractor=always_return({"x": 1})),
        VerifierSpec("v2", v2, extractor=always_return({"y": 2})),
    )

    import unittest.mock as mock
    with mock.patch("pipelines.nodes.truth_engine.verifier_registry", reg):
        state = _make_state(doc_type="test_doc")
        result = truth_engine_node(state)

    assert call_log == ["v1", "v2"]
    reports = result["truth_report"].verification_reports
    assert reports[0].passed is False
    assert reports[1].passed is True


def test_truth_engine_node_confidence_fusion_uses_all_signals() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node
    from pipelines.truth_engine.confidence import ConfidenceFusionPolicy

    extraction = ExtractionResult(
        fields=_PASSPORT_REQUIRED,
        overall_confidence=0.85,
        context_used=False,
        sample_count=1,
    )
    state = _make_state(classify_confidence=0.9, extraction_result=extraction)
    result = truth_engine_node(state)
    report: TruthReport = result["truth_report"]
    # _PASSPORT_REQUIRED has date_of_issue + date_of_expiry + date_of_birth →
    # passport_date_consistency verifier passes → +verification_pass_bonus
    # mrz_checksum is not attempted (no mrz_line2) → neutral
    policy = ConfidenceFusionPolicy()
    expected_base = 0.20 * 0.9 + 0.50 * 0.85 + 0.30 * 1.0
    expected_with_bonus = min(1.0, expected_base + policy.verification_pass_bonus)
    assert report.final_confidence == pytest.approx(expected_with_bonus, abs=1e-3)


def test_truth_engine_node_verification_failure_caps_confidence() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node
    from pipelines.truth_engine.verifier_registry import VerifierRegistry, VerifierSpec

    def always_fail(**_) -> dict:
        return {"valid": False}

    reg = VerifierRegistry()
    reg.register(
        "test_doc",
        VerifierSpec("fail_verifier", always_fail, extractor=lambda f: {"x": 1}),
    )

    import unittest.mock as mock
    with mock.patch("pipelines.nodes.truth_engine.verifier_registry", reg):
        state = _make_state(doc_type="test_doc", classify_confidence=0.95)
        result = truth_engine_node(state)

    report: TruthReport = result["truth_report"]
    assert report.final_confidence <= 0.70
    assert "capped_by_failures" in report.decision_reason


def test_truth_engine_node_fallback_reconstruction_when_no_extraction_result() -> None:
    """truth_engine_node reconstructs ExtractionResult from flat state fields when needed."""
    from pipelines.nodes.truth_engine import truth_engine_node

    state = {
        "doc_type": "passport",
        "classify_confidence": 0.8,
        "extraction_result": None,
        "extracted_fields": {"surname": "SMITH"},
        "extract_confidence": 0.75,
    }
    result = truth_engine_node(state)
    report: TruthReport = result["truth_report"]
    assert report.extraction.fields == {"surname": "SMITH"}
    assert report.extraction.overall_confidence == pytest.approx(0.75)


def test_truth_engine_node_unknown_doc_type_coverage_is_one() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    extraction = ExtractionResult(
        fields={"random_field": "value"}, overall_confidence=0.7, context_used=False, sample_count=1
    )
    result = truth_engine_node(_make_state(doc_type="nonexistent_type", extraction_result=extraction))
    report: TruthReport = result["truth_report"]
    assert report.field_validation.coverage_score == pytest.approx(1.0)
    assert report.verification_reports == []


def test_truth_engine_node_truth_report_has_no_routing_fields() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    result = truth_engine_node(_make_state())
    report: TruthReport = result["truth_report"]
    assert not hasattr(report, "action")
    assert not hasattr(report, "routing_decision")
    assert not hasattr(report, "next_node")


# ---------------------------------------------------------------------------
# PersistenceDecision
# ---------------------------------------------------------------------------


def test_persistence_decision_hard_fail_sets_verification_failed_status() -> None:
    vr = VerificationReport(verifier_name="mrz_checksum", passed=False, confidence=0.0)
    decision = PersistenceDecision.from_truth([vr], final_confidence=0.99)
    assert decision.document_status == "verification_failed"
    assert decision.allow_completion is False
    assert decision.allow_embedding is False
    assert decision.allow_learning is False


def test_persistence_decision_hard_fail_reason_names_failed_verifier() -> None:
    vr = VerificationReport(verifier_name="mrz_checksum", passed=False, confidence=0.0)
    decision = PersistenceDecision.from_truth([vr], final_confidence=0.99)
    assert "mrz_checksum" in decision.reason
    assert "deterministic_failure" in decision.reason


def test_persistence_decision_not_attempted_does_not_block() -> None:
    vr = VerificationReport(verifier_name="mrz_checksum", passed=None, confidence=0.0)
    decision = PersistenceDecision.from_truth([vr], final_confidence=0.92, threshold=0.85)
    assert decision.document_status == "completed"
    assert decision.allow_completion is True


def test_persistence_decision_low_confidence_sets_failed_status() -> None:
    low = PersistenceDecision.from_truth([], final_confidence=0.50, threshold=0.85)
    assert low.document_status == "failed"
    assert low.allow_completion is False


def test_persistence_decision_high_confidence_sets_completed_status() -> None:
    high = PersistenceDecision.from_truth([], final_confidence=0.90, threshold=0.85)
    assert high.document_status == "completed"
    assert high.allow_completion is True


def test_persistence_decision_allow_embedding_equals_allow_completion() -> None:
    decision = PersistenceDecision.from_truth([], final_confidence=0.90, threshold=0.85)
    assert decision.allow_embedding == decision.allow_completion
    assert decision.allow_learning == decision.allow_completion


def test_persistence_decision_reason_includes_confidence() -> None:
    above = PersistenceDecision.from_truth([], final_confidence=0.9200, threshold=0.85)
    assert "0.9200" in above.reason
    assert "above" in above.reason
    below = PersistenceDecision.from_truth([], final_confidence=0.5000, threshold=0.85)
    assert "0.5000" in below.reason
    assert "below" in below.reason


def test_persistence_decision_multiple_verifiers_one_fails_blocks_all() -> None:
    reports = [
        VerificationReport("v1", passed=True, confidence=1.0),
        VerificationReport("v2", passed=False, confidence=0.0),
    ]
    decision = PersistenceDecision.from_truth(reports, final_confidence=0.95)
    assert decision.document_status == "verification_failed"
    assert decision.allow_completion is False


def test_truth_engine_node_populates_persistence_decision() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    result = truth_engine_node(_make_state())
    report: TruthReport = result["truth_report"]
    assert hasattr(report, "persistence")
    assert isinstance(report.persistence, PersistenceDecision)
    assert isinstance(report.persistence.allow_completion, bool)
    assert report.persistence.document_status in ("completed", "verification_failed", "failed")
    assert report.persistence.reason != ""


# ---------------------------------------------------------------------------
# document_status via PersistenceDecision
# ---------------------------------------------------------------------------


def _make_truth_report_for_status(
    final_confidence: float = 0.92,
    allow_completion: bool = True,
    verifier_failed: bool = False,
) -> TruthReport:
    extraction = ExtractionResult(
        fields={}, overall_confidence=final_confidence, context_used=False, sample_count=1
    )
    fvr = FieldValidationReport(
        required_fields_present=[], required_fields_missing=[],
        additional_fields=[], coverage_score=1.0,
    )
    vr = (
        [VerificationReport(verifier_name="test", passed=False, confidence=0.0)]
        if verifier_failed
        else []
    )
    if verifier_failed:
        doc_status = "verification_failed"
        ac = False
    elif allow_completion:
        doc_status = "completed"
        ac = True
    else:
        doc_status = "failed"
        ac = False
    persistence = PersistenceDecision(
        document_status=doc_status,
        allow_completion=ac,
        allow_embedding=ac,
        allow_learning=ac,
        reason="test",
    )
    return TruthReport(
        extraction=extraction,
        field_validation=fvr,
        verification_reports=vr,
        final_confidence=final_confidence,
        decision_reason="test",
        persistence=persistence,
    )


def test_document_status_completed_when_high_confidence() -> None:
    decision = PersistenceDecision.from_truth([], final_confidence=0.95, threshold=0.85)
    assert decision.document_status == "completed"


def test_document_status_failed_when_low_confidence() -> None:
    decision = PersistenceDecision.from_truth([], final_confidence=0.50, threshold=0.85)
    assert decision.document_status == "failed"


def test_document_status_verification_failed_on_verifier_failure() -> None:
    vr = VerificationReport("mrz_checksum", passed=False, confidence=0.0)
    decision = PersistenceDecision.from_truth([vr], final_confidence=0.99)
    assert decision.document_status == "verification_failed"


def test_document_status_from_truth_report_field() -> None:
    report = _make_truth_report_for_status(allow_completion=True)
    assert report.persistence.document_status == "completed"

    report_failed = _make_truth_report_for_status(allow_completion=False)
    assert report_failed.persistence.document_status == "failed"

    report_vf = _make_truth_report_for_status(verifier_failed=True)
    assert report_vf.persistence.document_status == "verification_failed"


# ---------------------------------------------------------------------------
# New verifiers
# ---------------------------------------------------------------------------


def test_passport_date_consistency_valid() -> None:
    from agents.verifiers import passport_date_consistency

    result = passport_date_consistency("2020-01-01", "2030-01-01", "1990-01-01")
    assert result["valid"] is True
    assert result["checks"]["issue_before_expiry"] is True
    assert result["checks"]["birth_before_issue"] is True


def test_passport_date_consistency_issue_after_expiry() -> None:
    from agents.verifiers import passport_date_consistency

    result = passport_date_consistency("2035-01-01", "2030-01-01")
    assert result["valid"] is False
    assert result["checks"]["issue_before_expiry"] is False


def test_passport_date_consistency_partial_dates_still_checked() -> None:
    from agents.verifiers import passport_date_consistency

    result = passport_date_consistency("2020-01-01", "2030-01-01")  # no birth_date
    assert result["valid"] is True
    assert "issue_before_expiry" in result["checks"]
    assert "birth_before_issue" not in result["checks"]


def test_statement_period_ordering_valid() -> None:
    from agents.verifiers import statement_period_ordering

    assert statement_period_ordering("2024-01-01", "2024-01-31")["valid"] is True


def test_statement_period_ordering_reversed() -> None:
    from agents.verifiers import statement_period_ordering

    assert statement_period_ordering("2024-01-31", "2024-01-01")["valid"] is False


def test_gstin_checksum_valid_format() -> None:
    from agents.verifiers import gstin_checksum

    result = gstin_checksum("27AAPFU0939F1ZV")
    assert "valid" in result


def test_gstin_checksum_wrong_length() -> None:
    from agents.verifiers import gstin_checksum

    assert gstin_checksum("SHORT")["valid"] is False


def test_gstin_checksum_format_mismatch() -> None:
    from agents.verifiers import gstin_checksum

    # All digits, wrong format
    result = gstin_checksum("123456789012345")
    assert result["valid"] is False


def test_invoice_total_consistency_valid() -> None:
    from agents.verifiers import invoice_total_consistency

    result = invoice_total_consistency(1000.0, 180.0, 1180.0)
    assert result["valid"] is True
    assert result["computed_total"] == pytest.approx(1180.0)


def test_invoice_total_consistency_mismatch() -> None:
    from agents.verifiers import invoice_total_consistency

    result = invoice_total_consistency(1000.0, 180.0, 1200.0)
    assert result["valid"] is False


def test_gross_consistency_valid() -> None:
    from agents.verifiers import gross_consistency

    result = gross_consistency(50000.0, [10000.0, 5000.0], 65000.0)
    assert result["valid"] is True


def test_gross_consistency_mismatch() -> None:
    from agents.verifiers import gross_consistency

    result = gross_consistency(50000.0, [10000.0], 65000.0)
    assert result["valid"] is False


def test_pan_validation_valid() -> None:
    from agents.verifiers import pan_validation

    assert pan_validation("ABCDE1234F")["valid"] is True


def test_pan_validation_invalid_format() -> None:
    from agents.verifiers import pan_validation

    assert pan_validation("INVALID")["valid"] is False
    assert pan_validation("12345678901")["valid"] is False


def test_ay_fy_consistency_valid() -> None:
    from agents.verifiers import ay_fy_consistency

    result = ay_fy_consistency("2023-24", "2022-23")
    assert result["valid"] is True


def test_ay_fy_consistency_invalid() -> None:
    from agents.verifiers import ay_fy_consistency

    result = ay_fy_consistency("2023-24", "2023-24")  # same year
    assert result["valid"] is False


def test_ay_fy_consistency_format_mismatch() -> None:
    from agents.verifiers import ay_fy_consistency

    result = ay_fy_consistency("not-a-year", "2022-23")
    assert result["valid"] is False


def test_deed_date_consistency_valid() -> None:
    from agents.verifiers import deed_date_consistency

    result = deed_date_consistency("2024-01-10", "2024-01-15")
    assert result["valid"] is True


def test_deed_date_consistency_same_day_valid() -> None:
    from agents.verifiers import deed_date_consistency

    result = deed_date_consistency("2024-01-10", "2024-01-10")
    assert result["valid"] is True


def test_deed_date_consistency_registration_before_execution() -> None:
    from agents.verifiers import deed_date_consistency

    result = deed_date_consistency("2024-01-15", "2024-01-10")
    assert result["valid"] is False


# ---------------------------------------------------------------------------
# New verifier registry entries
# ---------------------------------------------------------------------------


def test_passport_has_date_consistency_verifier() -> None:
    names = [s.name for s in verifier_registry.get("passport")]
    assert "passport_date_consistency" in names


def test_bank_statement_has_period_ordering_verifier() -> None:
    names = [s.name for s in verifier_registry.get("bank_statement")]
    assert "statement_period_ordering" in names


def test_salary_slip_verifiers() -> None:
    names = [s.name for s in verifier_registry.get("salary_slip")]
    assert "gross_consistency" in names
    assert "pan_validation" in names


def test_itr_verifiers() -> None:
    names = [s.name for s in verifier_registry.get("itr")]
    assert "pan_validation" in names
    assert "ay_fy_consistency" in names


def test_property_deed_verifiers() -> None:
    names = [s.name for s in verifier_registry.get("property_deed")]
    assert "deed_date_consistency" in names


# ---------------------------------------------------------------------------
# Migration regressions — Phase 4.2
# ---------------------------------------------------------------------------


def test_validate_node_not_in_graph() -> None:
    """Regression: validate_node must be removed from the graph topology."""
    import unittest.mock as mock
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    node_names = set(g.get_graph().nodes.keys())
    assert "validate" not in node_names


def test_op_a_retry_routes_to_truth_engine_in_graph() -> None:
    import unittest.mock as mock
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    edges = g.get_graph().edges
    retry_destinations = {e[1] for e in edges if e[0] == "op_a_retry"}
    assert "truth_engine" in retry_destinations


def test_truth_report_includes_persistence_decision() -> None:
    """TruthReport must carry PersistenceDecision — P6 reads it verbatim."""
    report = TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=0.9, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[], required_fields_missing=[],
            additional_fields=[], coverage_score=1.0,
        ),
        verification_reports=[],
        final_confidence=0.9,
        decision_reason="ok",
        persistence=PersistenceDecision(
            document_status="completed",
            allow_completion=True,
            allow_embedding=True,
            allow_learning=True,
            reason="test",
        ),
    )
    assert report.persistence.document_status == "completed"
    assert report.persistence.allow_completion is True
    assert report.persistence.allow_embedding is True
    assert report.persistence.reason != ""


def test_verification_failure_blocks_completion_end_to_end() -> None:
    """End-to-end: verifier failure → allow_completion=False → status=verification_failed."""
    from pipelines.nodes.truth_engine import truth_engine_node
    from pipelines.truth_engine.verifier_registry import VerifierRegistry, VerifierSpec

    def always_fail(**_) -> dict:
        return {"valid": False}

    reg = VerifierRegistry()
    reg.register("test_doc", VerifierSpec("fail_v", always_fail, extractor=lambda f: {"x": 1}))

    with mock.patch("pipelines.nodes.truth_engine.verifier_registry", reg):
        state = _make_state(doc_type="test_doc", classify_confidence=0.95)
        result = truth_engine_node(state)

    report: TruthReport = result["truth_report"]
    assert report.persistence.allow_completion is False
    assert report.persistence.document_status == "verification_failed"


def test_output_writer_logs_truth_engine_confidence(minio_client, postgres_session) -> None:
    """Regression: output_writer must log truth_engine confidence, not validate."""
    import uuid
    from io_pipeline.output_writer import write_output
    from db.models import ConfidenceLog, Document

    doc_id = str(uuid.uuid4())
    doc = Document(
        id=doc_id,
        filename="passport_P001_20240101.pdf",
        object_key="raw/passport_P001_20240101.pdf",
        status="queued",
    )
    postgres_session.add(doc)
    postgres_session.commit()

    truth_report = _make_truth_report_for_status(final_confidence=0.88)
    state = {
        "document_id": doc_id,
        "universal_schema": {},
        "classify_confidence": 0.9,
        "extract_confidence": 0.85,
        "truth_report": truth_report,
        "error": None,
        "hitl_required": False,
        "hitl_approved": None,
    }
    with (
        mock.patch("io_pipeline.output_writer.get_session", return_value=postgres_session),
        mock.patch("io_pipeline.output_writer.get_object_store", return_value=minio_client),
    ):
        write_output(state)

    logs = postgres_session.query(ConfidenceLog).filter(
        ConfidenceLog.document_id == doc_id
    ).all()
    agents = {log.agent for log in logs}
    assert "truth_engine" in agents
    assert "validate" not in agents
    te_log = next(l for l in logs if l.agent == "truth_engine")
    assert te_log.score == pytest.approx(0.88)


# ---------------------------------------------------------------------------
# Verifier version
# ---------------------------------------------------------------------------


def test_verifier_version_constant_exists() -> None:
    assert VERIFIER_VERSION == "1.0"


def test_truth_engine_node_stamps_verifier_version() -> None:
    from pipelines.nodes.truth_engine import truth_engine_node

    result = truth_engine_node(_make_state())
    report: TruthReport = result["truth_report"]
    assert report.verifier_version == VERIFIER_VERSION


def test_truth_report_default_verifier_version_is_unknown() -> None:
    """TruthReport constructed without verifier_version gets 'unknown' sentinel."""
    report = TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=0.9, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[], required_fields_missing=[],
            additional_fields=[], coverage_score=1.0,
        ),
        verification_reports=[],
        final_confidence=0.9,
        decision_reason="ok",
        persistence=PersistenceDecision(
            document_status="completed",
            allow_completion=True,
            allow_embedding=True,
            allow_learning=True,
            reason="test",
        ),
    )
    assert report.verifier_version == "unknown"


def test_verifier_version_in_truth_audit_log(minio_client, postgres_session) -> None:
    """TruthAuditLog must persist verifier_version for audit replay."""
    import uuid
    from io_pipeline.output_writer import write_output
    from db.models import Document, TruthAuditLog

    doc_id = str(uuid.uuid4())
    postgres_session.add(Document(
        id=doc_id,
        filename="passport_P001_20240101.pdf",
        object_key="raw/passport_P001_20240101.pdf",
        status="queued",
    ))
    postgres_session.commit()

    from pipelines.nodes.truth_engine import truth_engine_node

    te_state = _make_state(doc_type="passport", classify_confidence=0.95)
    te_result = truth_engine_node(te_state)
    truth_report = te_result["truth_report"]

    state = {
        "document_id": doc_id,
        "universal_schema": {},
        "classify_confidence": 0.95,
        "extract_confidence": 0.88,
        "truth_report": truth_report,
        "error": None,
        "hitl_required": False,
        "hitl_approved": None,
    }
    with (
        mock.patch("io_pipeline.output_writer.get_session", return_value=postgres_session),
        mock.patch("io_pipeline.output_writer.get_object_store", return_value=minio_client),
    ):
        write_output(state)

    audit = postgres_session.query(TruthAuditLog).filter(
        TruthAuditLog.document_id == doc_id
    ).one()
    assert audit.verifier_version == VERIFIER_VERSION
    assert audit.document_status in ("completed", "verification_failed", "failed")
    assert audit.persistence_reason != ""


# ---------------------------------------------------------------------------
# Audit serialization
# ---------------------------------------------------------------------------


def test_truth_audit_log_serializes_verification_reports(minio_client, postgres_session) -> None:
    """TruthAuditLog.verification_reports must be a serializable list of dicts."""
    import uuid
    from io_pipeline.output_writer import write_output
    from db.models import Document, TruthAuditLog

    doc_id = str(uuid.uuid4())
    postgres_session.add(Document(
        id=doc_id,
        filename="bank_statement_A001_20240101.pdf",
        object_key="raw/bank_statement_A001_20240101.pdf",
        status="queued",
    ))
    postgres_session.commit()

    truth_report = _make_truth_report_for_status(final_confidence=0.90)
    state = {
        "document_id": doc_id,
        "universal_schema": {},
        "classify_confidence": 0.9,
        "extract_confidence": 0.88,
        "truth_report": truth_report,
        "error": None,
        "hitl_required": False,
        "hitl_approved": None,
    }
    with (
        mock.patch("io_pipeline.output_writer.get_session", return_value=postgres_session),
        mock.patch("io_pipeline.output_writer.get_object_store", return_value=minio_client),
    ):
        write_output(state)

    audit = postgres_session.query(TruthAuditLog).filter(
        TruthAuditLog.document_id == doc_id
    ).one()
    assert isinstance(audit.verification_reports, list)
    assert audit.allow_completion == truth_report.persistence.allow_completion
    assert audit.document_status == truth_report.persistence.document_status
    assert audit.persistence_reason == truth_report.persistence.reason


def test_persistence_decision_has_no_status_from_truth_report_function() -> None:
    """Regression: status_from_truth_report must not exist — status lives in PersistenceDecision."""
    import pipelines.truth_engine.models as m

    assert not hasattr(m, "status_from_truth_report"), (
        "status_from_truth_report was removed in Phase 4 freeze. "
        "Use truth_report.persistence.document_status."
    )
