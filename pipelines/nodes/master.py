from pipelines.state import GraphState


def master_node(state: GraphState) -> dict:
    """Parse filename to pre-populate doc_type when the pattern is unambiguous.

    Expected filename pattern: <doc_type>_<entity_id>_<YYYYMMDD>.<ext>
    Returns an empty dict when the filename does not match.
    """
    raise NotImplementedError
