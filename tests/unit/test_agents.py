import unittest.mock as mock

from agents.base import AgentResult
from agents.classify_agent import classify
from agents.llm_client import generate


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


def test_extract_returns_agent_result_for_passport() -> None:
    raise NotImplementedError


def test_extract_returns_failure_for_unknown_doc_type() -> None:
    raise NotImplementedError


def test_validate_returns_issues_for_invalid_fields() -> None:
    raise NotImplementedError


def test_validate_meets_threshold_true_above_threshold() -> None:
    raise NotImplementedError


def test_generate_returns_string() -> None:
    mock_response = mock.MagicMock()
    mock_response.text = "This is a Gemini response."
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        result = generate("Describe this document.")
    assert isinstance(result, str)
    assert len(result) > 0
