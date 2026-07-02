from pipelines.state import GraphState
from shared.utils.filename import parse_doc_type_from_filename


def master_node(state: GraphState) -> dict:
    """Parse filename to pre-populate doc_type when the pattern is unambiguous.

    Expected filename pattern: <doc_type>_<entity_id>_<YYYYMMDD>.<ext>
    Returns an empty dict when the filename does not match.
    """
    doc_type = parse_doc_type_from_filename(state["filename"])
    return {"doc_type": doc_type} if doc_type else {}
