from pydantic import BaseModel

from agents.base import AgentResult
from agents.llm_client import generate


class _ClassifyResponse(BaseModel):
    doc_type: str
    confidence: float


_PROMPT = (
    "Classify this document into exactly one type: "
    "passport, bank_statement, salary_slip, itr, gst, property_deed. "
    'Respond with JSON: {"doc_type": str, "confidence": float 0-1}.'
)


def classify(content: bytes, mime_type: str) -> AgentResult:
    """Classify document bytes and return doc_type + confidence."""
    try:
        raw = generate(
            _PROMPT, image_bytes=content, mime_type=mime_type, response_schema=_ClassifyResponse
        )
        parsed = _ClassifyResponse.model_validate_json(raw)
        return AgentResult(
            success=True,
            confidence=parsed.confidence,
            data={"doc_type": parsed.doc_type},
        )
    except Exception as e:
        return AgentResult(success=False, confidence=0.0, data={}, reason=str(e))
