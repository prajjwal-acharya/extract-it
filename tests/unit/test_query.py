def test_retrieve_returns_list_of_chunk_dicts() -> None:
    """retrieve() returns a list of dicts with document_id, chunk_text, chunk_index."""
    raise NotImplementedError


def test_retrieve_respects_top_k_limit() -> None:
    """retrieve() returns at most top_k results."""
    raise NotImplementedError


def test_synthesize_returns_non_empty_string() -> None:
    """synthesize() returns a non-empty answer string for valid inputs."""
    raise NotImplementedError


def test_synthesize_cites_document_ids() -> None:
    """synthesize() includes source document_id references in the answer."""
    raise NotImplementedError
