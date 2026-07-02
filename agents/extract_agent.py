from agents.base import AgentResult
from agents.llm_client import generate
from config.schema_loader import load_schema_model


def extract(content: bytes, mime_type: str, doc_type: str) -> AgentResult:
    """Extract structured fields from document bytes using the YAML schema for doc_type."""
    try:
        model = load_schema_model(doc_type)
    except FileNotFoundError as e:
        return AgentResult(success=False, confidence=0.0, data={}, reason=str(e))

    fields = [f for f in model.model_fields if f != "confidence"]
    prompt = f"Extract these fields from the document as JSON: {fields}"

    try:
        raw = generate(prompt, image_bytes=content, mime_type=mime_type, response_schema=model)
        parsed = model.model_validate_json(raw)
        return AgentResult(
            success=True,
            confidence=float(getattr(parsed, "confidence", 0.0)),
            data=parsed.model_dump(exclude={"confidence"}),
        )
    except Exception as e:
        return AgentResult(success=False, confidence=0.0, data={}, reason=str(e))
