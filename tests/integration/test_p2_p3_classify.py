"""Integration seam P2↔P3: classification output feeds the extraction node."""


def test_classify_output_is_valid_graph_state_update() -> None:
    """The classify node returns a dict whose keys are valid GraphState fields."""
    raise NotImplementedError


def test_doc_type_from_classify_is_used_by_extract_schema_lookup() -> None:
    """The doc_type returned by classify selects the correct YAML schema for extraction."""
    raise NotImplementedError


def test_parallel_classify_and_extract_merge_without_conflict() -> None:
    """Running classify and extract as parallel nodes merges their state updates cleanly."""
    raise NotImplementedError
