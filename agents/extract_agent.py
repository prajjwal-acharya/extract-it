import json
import logging

from google.genai import types

from agents.base import AgentResult
from agents.llm_client import generate, generate_with_tools
from agents.self_consistency import should_vote, vote
from agents.verifiers import balance_arithmetic, mrz_checksum
from config.schema_loader import load_schema_model

log = logging.getLogger(__name__)

MAX_TOOL_CALLS = 3

# FunctionDeclaration objects built once at import time.
_VERIFIER_DECLARATIONS = [
    types.FunctionDeclaration(
        name="mrz_checksum",
        description="Verify an MRZ field check digit per ICAO 9303",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "mrz_string": types.Schema(
                    type=types.Type.STRING, description="Field string excluding check digit"
                ),
                "check_digit": types.Schema(
                    type=types.Type.INTEGER, description="Check digit (0-9) as integer"
                ),
            },
            required=["mrz_string", "check_digit"],
        ),
    ),
    types.FunctionDeclaration(
        name="balance_arithmetic",
        description="Verify opening + sum(transactions) ≈ closing balance (±0.01)",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "opening": types.Schema(type=types.Type.NUMBER, description="Opening balance"),
                "closing": types.Schema(type=types.Type.NUMBER, description="Closing balance"),
                "transactions": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.NUMBER),
                    description="Transaction amounts (positive=credit, negative=debit)",
                ),
            },
            required=["opening", "closing", "transactions"],
        ),
    ),
]

_VERIFIER_REGISTRY = {"mrz_checksum": mrz_checksum, "balance_arithmetic": balance_arithmetic}

# Doc-types that benefit from a verifier pass.
_VERIFIABLE = {"passport", "bank_statement"}


def _extract_once(
    content: bytes, mime_type: str, doc_type: str, context: str | None = None
) -> AgentResult:
    """Single extraction pass — no self-consistency logic."""
    try:
        model = load_schema_model(doc_type)
    except FileNotFoundError as e:
        return AgentResult(success=False, confidence=0.0, data={}, reason=str(e))

    fields = [f for f in model.model_fields if f != "confidence"]
    prompt = f"Extract these fields from the document as JSON: {fields}"
    if context:
        prompt = f"{context}\n\n{prompt}"

    try:
        raw = generate(prompt, image_bytes=content, mime_type=mime_type, response_schema=model)
        parsed = model.model_validate_json(raw)
        extracted = parsed.model_dump(exclude={"confidence"})
        confidence = float(getattr(parsed, "confidence", 0.0))
    except Exception as e:
        return AgentResult(success=False, confidence=0.0, data={}, reason=str(e))

    # Verification pass: let the LLM call verifiers to confirm field consistency.
    tool_calls_made = 0
    verification_passed: bool | None = None
    if doc_type in _VERIFIABLE and extracted:
        verify_prompt = (
            f"Verify the extracted {doc_type} fields using the available tools. "
            f"Fields: {json.dumps(extracted)}"
        )
        try:
            _, tool_calls_made, tool_results = generate_with_tools(
                verify_prompt,
                declarations=_VERIFIER_DECLARATIONS,
                fn_registry=_VERIFIER_REGISTRY,
                max_tool_calls=MAX_TOOL_CALLS,
            )
            if tool_results:
                verification_passed = all(r["result"].get("valid", True) for r in tool_results)
        except Exception as e:
            log.warning("verifier pass failed for doc_type=%s: %s", doc_type, e)

    return AgentResult(
        success=True,
        confidence=confidence,
        data=extracted,
        tool_calls_made=tool_calls_made,
        verification_passed=verification_passed,
    )


def extract(
    content: bytes, mime_type: str, doc_type: str, context: str | None = None
) -> AgentResult:
    """Extract structured fields, applying self-consistency voting if confidence is borderline."""
    first = _extract_once(content, mime_type, doc_type, context)

    if not should_vote(first.confidence):
        return first

    samples = [first] + [_extract_once(content, mime_type, doc_type, context) for _ in range(2)]
    return vote(samples)
