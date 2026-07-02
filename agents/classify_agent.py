import json
from agents.base import AgentResult
from agents.llm_client import generate

_PROMPT = """You are a document classifier. Given the document content, identify the document type.
Return JSON: {{"doc_type": "<type>", "confidence": <0-1>, "reason": "<explanation>"}}
Document types: passport, bank_statement, invoice, contract, unknown

Document content:
{content}"""


def classify(content: str) -> AgentResult:
    response = generate(_PROMPT.format(content=content))
    parsed = json.loads(response)
    return AgentResult(
        success=True,
        confidence=parsed["confidence"],
        data={"doc_type": parsed["doc_type"]},
        reason=parsed.get("reason"),
    )
