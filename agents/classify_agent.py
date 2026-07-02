from agents.base import AgentResult


def classify(content: str) -> AgentResult:
    """Classify document content and return doc_type + confidence.

    Sends content to the Gemini model with a classification prompt and
    parses the structured JSON response.
    """
    raise NotImplementedError
