from agents.base import AgentResult


def extract(content: str, doc_type: str) -> AgentResult:
    """Extract structured fields from document content using the YAML schema for doc_type.

    Loads the field list from config/schemas/<doc_type>.yaml, builds a prompt,
    and parses the Gemini response into an AgentResult.
    """
    raise NotImplementedError
