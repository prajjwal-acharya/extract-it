from pipelines.state import GraphState


def normalize_node(state: GraphState) -> dict:
    """Map doc-type-specific extracted fields to the universal schema.

    Reads the field-mapping table for state['doc_type'] and projects
    extracted_fields into the canonical universal_schema dict.
    """
    raise NotImplementedError
