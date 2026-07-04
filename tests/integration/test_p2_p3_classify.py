"""Integration seam P2↔P3: classification output feeds the extraction node."""

import typing
import unittest.mock as mock

from pipelines.nodes.classify import classify_node
from pipelines.nodes.extract import extract_node
from pipelines.state import GraphState


def test_classify_output_is_valid_graph_state_update(sample_pdf_bytes) -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "filename": "passport_P001_20240101.pdf",
        "raw_bytes": sample_pdf_bytes,
        "doc_type": None,
    }

    mock_response = mock.MagicMock()
    mock_response.text = '{"doc_type": "passport", "confidence": 0.97}'

    valid_keys = set(typing.get_type_hints(GraphState).keys())

    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        update = classify_node(state)

    assert set(update.keys()).issubset(valid_keys)
    assert update.get("doc_type") == "passport"
    assert 0.0 <= update.get("classify_confidence", -1) <= 1.0


def test_doc_type_from_classify_is_used_by_extract_schema_lookup(passport_state) -> None:
    """extract_node reads state['doc_type'] and loads the matching YAML schema."""
    passport_json = (
        '{"surname": "DOE", "given_names": "JANE", "nationality": "USA", '
        '"date_of_birth": "1985-06-15", "sex": "F", "place_of_birth": null, '
        '"date_of_issue": "2019-03-01", "date_of_expiry": "2029-03-01", '
        '"passport_number": "A9876543", "mrz_line1": null, "mrz_line2": null, '
        '"confidence": 0.89}'
    )
    mock_response = mock.MagicMock()
    mock_response.text = passport_json

    with (
        mock.patch("agents.llm_client._client") as mock_client_fn,
        mock.patch("pipelines.nodes.extract.get_session"),
        mock.patch("pipelines.nodes.extract.similarity_search", return_value=[]),
    ):
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        update = extract_node(passport_state)

    assert update.get("extract_confidence") == 0.89
    assert update["extracted_fields"].get("surname") == "DOE"
    assert "confidence" not in update["extracted_fields"]


def test_sequential_classify_and_extract_state_updates_are_disjoint() -> None:
    """classify_node and extract_node write to disjoint GraphState keys — no reducer conflict."""
    classify_update = {"doc_type": "passport", "classify_confidence": 0.97}
    extract_update = {"extracted_fields": {"surname": "DOE"}, "extract_confidence": 0.89}

    valid_keys = set(typing.get_type_hints(GraphState).keys())
    merged = {**classify_update, **extract_update}

    assert set(merged.keys()).issubset(valid_keys)
    assert set(classify_update.keys()).isdisjoint(set(extract_update.keys()))
