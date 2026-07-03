import unittest.mock as mock

from agents.base import AgentResult
from agents.classify_agent import classify
from agents.extract_agent import extract
from agents.llm_client import generate
from agents.validate_agent import meets_threshold, validate


def test_classify_returns_agent_result(sample_pdf_bytes) -> None:
    mock_response = mock.MagicMock()
    mock_response.text = '{"doc_type": "passport", "confidence": 0.95}'
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        result = classify(sample_pdf_bytes, "application/pdf")
    assert isinstance(result, AgentResult)
    assert result.data.get("doc_type") == "passport"


def test_classify_confidence_is_between_zero_and_one(sample_pdf_bytes) -> None:
    mock_response = mock.MagicMock()
    mock_response.text = '{"doc_type": "bank_statement", "confidence": 0.82}'
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        result = classify(sample_pdf_bytes, "application/pdf")
    assert 0.0 <= result.confidence <= 1.0


def test_extract_returns_agent_result_for_passport(sample_pdf_bytes) -> None:
    passport_json = (
        '{"surname": "SMITH", "given_names": "JOHN", "nationality": "GBR", '
        '"date_of_birth": "1990-01-01", "sex": "M", "place_of_birth": null, '
        '"date_of_issue": "2020-01-01", "date_of_expiry": "2030-01-01", '
        '"passport_number": "P1234567", "mrz_line1": null, "mrz_line2": null, '
        '"confidence": 0.91}'
    )
    mock_response = mock.MagicMock()
    mock_response.text = passport_json
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        result = extract(sample_pdf_bytes, "application/pdf", "passport")
    assert isinstance(result, AgentResult)
    assert result.success is True
    assert result.data.get("surname") == "SMITH"
    assert "confidence" not in result.data


def test_extract_accepts_optional_context_param(sample_pdf_bytes) -> None:
    passport_json = (
        '{"surname": "JONES", "given_names": "ALICE", "nationality": "GBR", '
        '"date_of_birth": "1985-06-15", "sex": "F", "place_of_birth": null, '
        '"date_of_issue": "2019-01-01", "date_of_expiry": "2029-01-01", '
        '"passport_number": "P9876543", "mrz_line1": null, "mrz_line2": null, '
        '"confidence": 0.88}'
    )
    mock_response = mock.MagicMock()
    mock_response.text = passport_json
    captured_prompt: list[str] = []

    original_generate = __import__("agents.llm_client", fromlist=["generate"]).generate

    def capture_generate(prompt, **kwargs):
        captured_prompt.append(prompt)
        return original_generate.__wrapped__(prompt, **kwargs) if hasattr(original_generate, "__wrapped__") else passport_json

    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        result = extract(sample_pdf_bytes, "application/pdf", "passport", context="Example: prior extraction")

    assert result.success is True
    assert result.data.get("surname") == "JONES"


def test_extract_returns_failure_for_unknown_doc_type(sample_pdf_bytes) -> None:
    result = extract(sample_pdf_bytes, "application/pdf", "nonexistent_type")
    assert result.success is False
    assert result.confidence == 0.0
    assert "nonexistent_type" in (result.reason or "")


def test_validate_returns_issues_for_invalid_fields() -> None:
    # Missing all required passport fields — should produce issues
    result = validate("passport", {"surname": "SMITH"})
    assert isinstance(result.data.get("issues"), list)
    assert len(result.data["issues"]) > 0
    assert result.confidence < 1.0


def test_validate_meets_threshold_true_above_threshold() -> None:
    assert meets_threshold(0.95) is True
    assert meets_threshold(0.85) is True  # exactly at threshold
    assert meets_threshold(0.84) is False


def test_generate_returns_string() -> None:
    mock_response = mock.MagicMock()
    mock_response.text = "This is a Gemini response."
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        result = generate("Describe this document.")
    assert isinstance(result, str)
    assert len(result) > 0
