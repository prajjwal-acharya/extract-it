import json
import yaml
from pathlib import Path
from agents.base import AgentResult
from agents.llm_client import generate

_SCHEMA_DIR = Path(__file__).parent.parent / "config" / "schemas"

_PROMPT = """Extract the following fields from the document content according to the schema.
Return JSON: {{"fields": {{...}}, "confidence": <0-1>, "reason": "<explanation>"}}

Schema fields: {fields}

Document content:
{content}"""


def extract(content: str, doc_type: str) -> AgentResult:
    schema_path = _SCHEMA_DIR / f"{doc_type}.yaml"
    if not schema_path.exists():
        return AgentResult(success=False, confidence=0.0, data={}, reason=f"No schema for {doc_type}")

    schema = yaml.safe_load(schema_path.read_text())
    fields_desc = json.dumps([f["name"] for f in schema["fields"]])
    response = generate(_PROMPT.format(fields=fields_desc, content=content))
    parsed = json.loads(response)
    return AgentResult(
        success=True,
        confidence=parsed["confidence"],
        data=parsed["fields"],
        reason=parsed.get("reason"),
    )
