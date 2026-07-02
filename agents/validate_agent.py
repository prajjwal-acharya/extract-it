import json
from agents.base import AgentResult
from agents.llm_client import generate
from config.settings import settings

_PROMPT = """Validate the extracted fields for a {doc_type} document.
Check for: completeness, format correctness, logical consistency.
Return JSON: {{"valid": <bool>, "confidence": <0-1>, "issues": [<list of issues>], "reason": "<summary>"}}

Extracted fields:
{fields}"""


def validate(doc_type: str, extracted_fields: dict) -> AgentResult:
    import json as _json
    response = generate(_PROMPT.format(doc_type=doc_type, fields=_json.dumps(extracted_fields, indent=2)))
    parsed = json.loads(response)
    confidence = parsed["confidence"] if parsed["valid"] else parsed["confidence"] * 0.5
    return AgentResult(
        success=parsed["valid"],
        confidence=confidence,
        data={"issues": parsed.get("issues", [])},
        reason=parsed.get("reason"),
    )


def meets_threshold(confidence: float) -> bool:
    return confidence >= settings.CONFIDENCE_THRESHOLD
