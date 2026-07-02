"""Integration seam P3↔P4: extraction output is consumable by the validation node."""
import typing
import unittest.mock as mock

from config.schema_loader import load_schema_model
from pipelines.nodes.extract import extract_node
from pipelines.nodes.validate import validate_node
from pipelines.state import GraphState


def test_extract_output_keys_match_schema_fields(passport_state) -> None:
    passport_json = (
        '{"surname": "LEE", "given_names": "ANNA", "nationality": "SGP", '
        '"date_of_birth": "1992-03-10", "sex": "F", "place_of_birth": null, '
        '"date_of_issue": "2021-06-01", "date_of_expiry": "2031-06-01", '
        '"passport_number": "S7654321", "mrz_line1": null, "mrz_line2": null, '
        '"confidence": 0.88}'
    )
    mock_response = mock.MagicMock()
    mock_response.text = passport_json

    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        update = extract_node(passport_state)

    schema_fields = {f for f in load_schema_model("passport").model_fields if f != "confidence"}
    assert set(update["extracted_fields"].keys()) == schema_fields


def test_extract_output_is_valid_graph_state_update(passport_state) -> None:
    passport_json = (
        '{"surname": "KIM", "given_names": "JAMES", "nationality": "KOR", '
        '"date_of_birth": "1988-11-22", "sex": "M", "place_of_birth": null, '
        '"date_of_issue": "2018-01-15", "date_of_expiry": "2028-01-15", '
        '"passport_number": "M1122334", "mrz_line1": null, "mrz_line2": null, '
        '"confidence": 0.93}'
    )
    mock_response = mock.MagicMock()
    mock_response.text = passport_json

    valid_keys = set(typing.get_type_hints(GraphState).keys())

    with mock.patch("agents.llm_client._client") as mock_client_fn:
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        update = extract_node(passport_state)

    assert set(update.keys()).issubset(valid_keys)
    assert "extracted_fields" in update
    assert "extract_confidence" in update


def test_validate_receives_extracted_fields_and_doc_type(passport_state) -> None:
    extracted = {
        "surname": "PATEL", "given_names": "RAJ", "nationality": "IND",
        "date_of_birth": "1995-07-04", "sex": "M", "place_of_birth": "Mumbai",
        "date_of_issue": "2022-01-01", "date_of_expiry": "2032-01-01",
        "passport_number": "Z9988776", "mrz_line1": None, "mrz_line2": None,
    }
    state: GraphState = {  # type: ignore[typeddict-item]
        **passport_state,
        "extracted_fields": extracted,
    }
    update = validate_node(state)
    assert "validation_issues" in update
    assert "validate_confidence" in update
    assert isinstance(update["validation_issues"], list)
    assert update["validate_confidence"] == 1.0
