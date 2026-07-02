from agents.base import AgentResult


def validate(doc_type: str, extracted_fields: dict) -> AgentResult:
    """Validate extracted fields for completeness, format, and logical consistency.

    Sends the extracted fields to Gemini with a validation prompt and returns
    a confidence score plus a list of discovered issues.
    """
    raise NotImplementedError


def meets_threshold(confidence: float) -> bool:
    """Return True if confidence meets or exceeds the configured CONFIDENCE_THRESHOLD."""
    raise NotImplementedError
