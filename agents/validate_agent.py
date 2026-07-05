from pydantic import ValidationError

from agents.base import AgentResult
from config.schema_loader import load_schema_model
from config.settings import settings
from pipelines.registry import registry as _registry


def validate(doc_type: str, extracted_fields: dict) -> AgentResult:
    """Validate extracted fields against the doc_type's YAML schema.

    Confidence = 1 - (failing_field_count / total_schema_fields).
    No LLM call — deterministic, cheap, reuses the already-built schema model.
    """
    try:
        model = load_schema_model(_registry.reference_schema_name(doc_type))
    except FileNotFoundError as e:
        return AgentResult(success=False, confidence=0.0, data={"issues": [str(e)]}, reason=str(e))

    fields_to_check = {k: v for k, v in extracted_fields.items() if k != "confidence"}
    try:
        model(**fields_to_check, confidence=0.0)
        return AgentResult(success=True, confidence=1.0, data={"issues": []})
    except ValidationError as e:
        issues = [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]
        total = len(model.model_fields) - 1  # exclude injected confidence field
        confidence = max(0.0, 1 - len(issues) / total) if total else 0.0
        return AgentResult(success=False, confidence=confidence, data={"issues": issues})


def meets_threshold(confidence: float) -> bool:
    """Return True if confidence meets or exceeds the configured CONFIDENCE_THRESHOLD."""
    return confidence >= settings.CONFIDENCE_THRESHOLD
