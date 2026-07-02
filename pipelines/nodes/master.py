from adapters.factory import get_object_store
from pipelines.state import GraphState
from shared.utils.filename import parse_doc_type_from_filename


def master_node(state: GraphState) -> dict:
    """Fetch raw bytes once and pre-populate doc_type from filename when unambiguous.

    All downstream nodes (classify, extract) read state["raw_bytes"] rather than
    re-fetching — one object-store call per document regardless of retry count.
    """
    raw_bytes = get_object_store().get(state["object_key"])
    update: dict = {"raw_bytes": raw_bytes}
    doc_type = parse_doc_type_from_filename(state["filename"])
    if doc_type:
        update["doc_type"] = doc_type
    return update
