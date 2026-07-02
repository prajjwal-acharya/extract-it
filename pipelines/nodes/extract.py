from agents.extract_agent import extract
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename


def extract_node(state: GraphState) -> dict:
    """Run extract agent on state['raw_bytes'] using state's doc_type, return GraphState update."""
    mime_type = mime_from_filename(state["filename"])
    result = extract(state["raw_bytes"], mime_type, state.get("doc_type") or "")
    update: dict = {"extracted_fields": result.data, "extract_confidence": result.confidence}
    if not result.success:
        update["error"] = result.reason
    return update
