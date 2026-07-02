from adapters.factory import get_object_store
from agents.classify_agent import classify
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename


def classify_node(state: GraphState) -> dict:
    """Fetch raw bytes, run classify agent, return GraphState update."""
    store = get_object_store()
    data = store.get(state["object_key"])
    mime_type = mime_from_filename(state["filename"])

    result = classify(data, mime_type)
    return {
        "doc_type": result.data.get("doc_type") if result.success else state.get("doc_type"),
        "classify_confidence": result.confidence,
    }
