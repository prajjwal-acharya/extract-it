from pipelines.state import GraphState


def op_a_retry_node(state: GraphState) -> dict:
    """Re-run extraction augmented with RAG context from pgvector similarity search.

    Retrieves the top-k similar document chunks, augments the extraction prompt,
    calls extract_agent and validate_agent again, and increments retry_count.
    """
    raise NotImplementedError
