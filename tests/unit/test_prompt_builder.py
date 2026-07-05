"""Tests for the extraction prompt builder — schema-guided open extraction."""
import unittest.mock as mock

import pytest

from agents.prompt_builder import build_extraction_prompt


# ---------------------------------------------------------------------------
# Reference schema fields appear in the prompt
# ---------------------------------------------------------------------------


def test_prompt_contains_required_fields_for_passport() -> None:
    prompt = build_extraction_prompt("passport")
    for field in ("surname", "given_names", "passport_number", "nationality"):
        assert field in prompt, f"Required field {field!r} missing from prompt"


def test_prompt_contains_optional_fields_for_passport() -> None:
    prompt = build_extraction_prompt("passport")
    # place_of_birth, mrz_line1, mrz_line2 are optional in the passport schema
    assert "place_of_birth" in prompt or "mrz_line1" in prompt


def test_prompt_labels_required_fields_as_business_critical() -> None:
    prompt = build_extraction_prompt("passport")
    assert "business-critical" in prompt.lower()


# ---------------------------------------------------------------------------
# Open extraction instructions are present
# ---------------------------------------------------------------------------


def test_prompt_instructs_extraction_of_additional_fields() -> None:
    prompt = build_extraction_prompt("passport")
    assert "every other" in prompt.lower() or "additional" in prompt.lower()


def test_prompt_never_restricts_to_schema_fields_only() -> None:
    prompt = build_extraction_prompt("passport")
    # The phrase "only" near field list names would suggest constraint — must not appear
    # as an instruction to Gemini
    assert "extract only" not in prompt.lower()
    assert "limit yourself" not in prompt.lower()


# ---------------------------------------------------------------------------
# Extraction envelope is specified in the prompt
# ---------------------------------------------------------------------------


def test_prompt_specifies_fields_key_in_envelope() -> None:
    prompt = build_extraction_prompt("passport")
    assert '"fields"' in prompt


def test_prompt_specifies_overall_confidence_key_in_envelope() -> None:
    prompt = build_extraction_prompt("passport")
    assert "overall_confidence" in prompt


# ---------------------------------------------------------------------------
# Context is prepended correctly
# ---------------------------------------------------------------------------


def test_prompt_prepends_context_when_provided() -> None:
    context = "Example: prior extraction data"
    prompt = build_extraction_prompt("passport", context=context)
    assert prompt.startswith(context)
    # Schema guidance still present
    assert "surname" in prompt
    assert "overall_confidence" in prompt


def test_prompt_without_context_starts_with_extract_instruction() -> None:
    prompt = build_extraction_prompt("passport")
    assert prompt.startswith("Extract")


# ---------------------------------------------------------------------------
# Unknown doc type — open extraction still works
# ---------------------------------------------------------------------------


def test_prompt_for_unknown_doc_type_still_has_envelope() -> None:
    prompt = build_extraction_prompt("nonexistent_type")
    assert '"fields"' in prompt
    assert "overall_confidence" in prompt


def test_prompt_for_unknown_doc_type_has_no_field_lists() -> None:
    prompt = build_extraction_prompt("nonexistent_type")
    assert "business-critical" not in prompt.lower()


def test_prompt_for_unknown_doc_type_instructs_open_extraction() -> None:
    prompt = build_extraction_prompt("nonexistent_type")
    assert "every" in prompt.lower() or "all" in prompt.lower()


# ---------------------------------------------------------------------------
# Extraction envelope parsing — stable contract and graceful fallback
# ---------------------------------------------------------------------------


def test_extract_envelope_fields_are_preserved_in_result(sample_pdf_bytes) -> None:
    """All fields inside the envelope are passed through to AgentResult.data."""
    from agents.extract_agent import extract

    response = (
        '{"fields": {"full_name": "SARA ALI", "dob": "1990-05-01", '
        '"extra_field": "unexpected_value"}, "overall_confidence": 0.93}'
    )
    mock_resp = mock.MagicMock()
    mock_resp.text = response
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_resp
        result = extract(sample_pdf_bytes, "application/pdf", "passport")

    assert result.success is True
    assert result.fields["full_name"] == "SARA ALI"
    assert result.fields["dob"] == "1990-05-01"
    # Extra field discovered by Gemini must be preserved
    assert result.fields["extra_field"] == "unexpected_value"
    assert result.overall_confidence == pytest.approx(0.93)
    # Envelope keys must not leak into extracted fields
    assert "overall_confidence" not in result.fields
    assert "fields" not in result.fields


def test_extract_additional_fields_not_in_schema_are_preserved(sample_pdf_bytes) -> None:
    """Fields Gemini discovers that are absent from the reference schema must not be discarded."""
    from agents.extract_agent import extract

    response = (
        '{"fields": {"surname": "SMITH", "issued_by": "Home Office", '
        '"biometric_chip": true, "visa_stamps": ["USA", "UK"]}, '
        '"overall_confidence": 0.91}'
    )
    mock_resp = mock.MagicMock()
    mock_resp.text = response
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_resp
        result = extract(sample_pdf_bytes, "application/pdf", "passport")

    assert result.fields["surname"] == "SMITH"
    assert result.fields["issued_by"] == "Home Office"
    assert result.fields["biometric_chip"] is True
    assert result.fields["visa_stamps"] == ["USA", "UK"]


def test_extract_envelope_fallback_on_flat_response(sample_pdf_bytes) -> None:
    """Graceful fallback: flat dict response (missing 'fields' key) is treated as fields."""
    from agents.extract_agent import extract

    flat_response = '{"full_name": "FLAT RESPONSE", "id": "99"}'
    mock_resp = mock.MagicMock()
    mock_resp.text = flat_response
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_resp
        result = extract(sample_pdf_bytes, "application/pdf", "passport")

    assert result.success is True
    assert result.fields.get("full_name") == "FLAT RESPONSE"


def test_extract_required_schema_fields_present_when_extracted(sample_pdf_bytes) -> None:
    """When Gemini extracts business-critical fields, they appear in result.data."""
    from agents.extract_agent import extract

    response = (
        '{"fields": {"surname": "DOE", "given_names": "JANE", "nationality": "IND", '
        '"date_of_birth": "1988-03-12", "sex": "F", "date_of_issue": "2021-01-01", '
        '"date_of_expiry": "2031-01-01", "passport_number": "Z9876543"}, '
        '"overall_confidence": 0.95}'
    )
    mock_resp = mock.MagicMock()
    mock_resp.text = response
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_resp
        result = extract(sample_pdf_bytes, "application/pdf", "passport")

    required = ("surname", "given_names", "nationality", "date_of_birth", "passport_number")
    for field in required:
        assert field in result.fields, f"Required field {field!r} missing from result"
