from pipelines.state import GraphState


def write_output(state: GraphState) -> None:
    """Persist pipeline results to Postgres and the object store.

    Updates the Document row (status, universal_schema), appends a ConfidenceLog
    entry, and writes the JSON-serialised universal_schema to output/<doc_id>.json
    in the object store.
    """
    raise NotImplementedError
