"""Integration seam P3↔P4: extraction output is consumable by the validation node."""


def test_extract_output_keys_match_schema_fields() -> None:
    """extracted_fields from the extract node contains all required schema field names."""
    raise NotImplementedError


def test_extract_output_is_valid_graph_state_update() -> None:
    """The extract node returns a dict whose keys are valid GraphState fields."""
    raise NotImplementedError


def test_validate_receives_extracted_fields_and_doc_type() -> None:
    """validate_node() is called with both doc_type and extracted_fields from prior nodes."""
    raise NotImplementedError
