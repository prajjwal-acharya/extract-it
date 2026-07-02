from adapters.factory import get_object_store
from agents.extract_agent import extract
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename


def extract_node(state: GraphState) -> dict:
    """Fetch raw bytes, run extract agent using state's doc_type, return GraphState update."""
    store = get_object_store()
    data = store.get(state["object_key"])
    mime_type = mime_from_filename(state["filename"])

    result = extract(data, mime_type, state.get("doc_type") or "")
    return {"extracted_fields": result.data, "extract_confidence": result.confidence}
