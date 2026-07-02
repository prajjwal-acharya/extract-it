from agents.validate_agent import validate
from pipelines.state import GraphState


def validate_node(state: GraphState) -> dict:
    """Validate extracted_fields against doc_type's schema, return GraphState update."""
    result = validate(state.get("doc_type") or "", state.get("extracted_fields") or {})
    return {
        "validation_issues": result.data.get("issues", []),
        "validate_confidence": result.confidence,
    }
