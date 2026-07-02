from agents.classify_agent import classify
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename


def classify_node(state: GraphState) -> dict:
    """Run classify agent on state['raw_bytes'], return GraphState update."""
    mime_type = mime_from_filename(state["filename"])
    result = classify(state["raw_bytes"], mime_type)
    update: dict = {
        "doc_type": result.data.get("doc_type") if result.success else state.get("doc_type"),
        "classify_confidence": result.confidence,
    }
    if not result.success:
        update["error"] = result.reason
    return update
