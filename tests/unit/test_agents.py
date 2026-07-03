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


def test_embed_passes_task_type_to_config() -> None:
    """embed() forwards task_type to EmbedContentConfig."""
    from agents.llm_client import embed

    fake_embedding = mock.MagicMock()
    fake_embedding.values = [0.1] * 768
    fake_response = mock.MagicMock()
    fake_response.embeddings = [fake_embedding]

    with mock.patch("agents.llm_client._client") as mock_client_fn, \
         mock.patch("agents.llm_client.types.EmbedContentConfig") as mock_config:
        mock_client_fn.return_value.models.embed_content.return_value = fake_response
        embed("test text", task_type="RETRIEVAL_QUERY")

    mock_config.assert_called_once()
    call_kwargs = mock_config.call_args[1]
    assert call_kwargs.get("task_type") == "RETRIEVAL_QUERY"


def test_generate_returns_string() -> None:
    mock_response = mock.MagicMock()
    mock_response.text = "This is a Gemini response."
    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        result = generate("Describe this document.")
    assert isinstance(result, str)
    assert len(result) > 0


# ── Verifier unit tests ─────────────────────────────────────────────────────

def test_mrz_checksum_valid_digit() -> None:
    from agents.verifiers import mrz_checksum

    # "SMITH" → S=28,M=22,I=18,T=29,H=17 with weights 7,3,1,7,3
    # 28*7 + 22*3 + 18*1 + 29*7 + 17*3 = 196+66+18+203+51 = 534 → 534%10 = 4
    result = mrz_checksum("SMITH", 4)
    assert result["valid"] is True
    assert result["expected"] == 4


def test_mrz_checksum_invalid_digit() -> None:
    from agents.verifiers import mrz_checksum

    result = mrz_checksum("SMITH", 9)
    assert result["valid"] is False
    assert result["got"] == 9


def test_mrz_checksum_filler_char() -> None:
    from agents.verifiers import mrz_checksum

    # '<' has value 0 — padding should not affect validity
    result = mrz_checksum("<<<<<", 0)
    assert result["valid"] is True


def test_balance_arithmetic_reconciles() -> None:
    from agents.verifiers import balance_arithmetic

    result = balance_arithmetic(opening=1000.00, closing=1150.50, transactions=[200.50, -50.00])
    assert result["valid"] is True
    assert result["computed_closing"] == 1150.50


def test_balance_arithmetic_mismatch() -> None:
    from agents.verifiers import balance_arithmetic

    result = balance_arithmetic(opening=1000.00, closing=1200.00, transactions=[100.00])
    assert result["valid"] is False
    assert result["computed_closing"] == 1100.00


def test_balance_arithmetic_within_tolerance() -> None:
    from agents.verifiers import balance_arithmetic

    # Floating-point accumulation within ±0.01 should pass
    result = balance_arithmetic(opening=0.1, closing=0.3, transactions=[0.1, 0.1])
    assert result["valid"] is True


# ── Tool-call ceiling test ───────────────────────────────────────────────────

def test_extract_tool_call_ceiling(sample_pdf_bytes) -> None:
    """generate_with_tools must not exceed MAX_TOOL_CALLS even if the model keeps requesting tools."""
    from agents.extract_agent import MAX_TOOL_CALLS
    from agents.llm_client import generate_with_tools
    from google.genai import types

    call_count = 0

    def always_fn_call(*args, **kwargs):
        """Simulate a model that always returns a function_call part."""
        part = mock.MagicMock()
        part.function_call = mock.MagicMock()
        part.function_call.name = "mrz_checksum"
        part.function_call.args = {"mrz_string": "ABC", "check_digit": 1}
        candidate = mock.MagicMock()
        candidate.content = mock.MagicMock()
        candidate.content.parts = [part]
        resp = mock.MagicMock()
        resp.candidates = [candidate]
        resp.text = None
        nonlocal call_count
        call_count += 1
        return resp

    dummy_decl = types.FunctionDeclaration(
        name="mrz_checksum",
        description="test",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"mrz_string": types.Schema(type=types.Type.STRING), "check_digit": types.Schema(type=types.Type.INTEGER)},
            required=["mrz_string", "check_digit"],
        ),
    )
    from agents.verifiers import mrz_checksum

    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.side_effect = always_fn_call
        _, calls_made = generate_with_tools(
            "verify fields",
            declarations=[dummy_decl],
            fn_registry={"mrz_checksum": mrz_checksum},
            max_tool_calls=MAX_TOOL_CALLS,
        )

    assert calls_made <= MAX_TOOL_CALLS
