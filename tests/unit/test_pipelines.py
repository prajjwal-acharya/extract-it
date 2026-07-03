import operator
import typing
import unittest.mock as mock

from pipelines.nodes.master import master_node
from pipelines.router import route_after_validate
from pipelines.state import GraphState


def test_graph_state_is_valid_typed_dict() -> None:
    hints = typing.get_type_hints(GraphState)
    assert "document_id" in hints
    assert "doc_type" in hints
    assert "status" in hints


def test_parallel_fields_have_annotated_reducers() -> None:
    hints = typing.get_type_hints(GraphState, include_extras=True)
    # extracted_fields must be Annotated
    assert typing.get_origin(hints["extracted_fields"]) is typing.Annotated
    # validation_issues must be Annotated
    assert typing.get_origin(hints["validation_issues"]) is typing.Annotated


def test_validation_issues_uses_add_reducer() -> None:
    hints = typing.get_type_hints(GraphState, include_extras=True)
    args = typing.get_args(hints["validation_issues"])
    # args[1] is the reducer; operator.add for lists performs concatenation
    assert args[1] is operator.add


def test_master_node_parses_filename_pattern() -> None:
    pdf_bytes = b"%PDF-1.4 stub"
    state: GraphState = {  # type: ignore[typeddict-item]
        "filename": "bank_statement_ACC001_20240101.pdf",
        "object_key": "raw/bank_statement_ACC001_20240101.pdf",
    }
    with mock.patch("pipelines.nodes.master.get_object_store") as mock_store:
        mock_store.return_value.get.return_value = pdf_bytes
        result = master_node(state)
    assert result.get("doc_type") == "bank_statement"
    assert result.get("raw_bytes") == pdf_bytes


def test_master_node_sets_raw_bytes_for_unmatched_filename() -> None:
    pdf_bytes = b"%PDF-1.4 stub"
    state: GraphState = {  # type: ignore[typeddict-item]
        "filename": "random_document.pdf",
        "object_key": "raw/random_document.pdf",
    }
    with mock.patch("pipelines.nodes.master.get_object_store") as mock_store:
        mock_store.return_value.get.return_value = pdf_bytes
        result = master_node(state)
    assert result.get("raw_bytes") == pdf_bytes
    assert "doc_type" not in result


def test_normalize_node_produces_universal_schema() -> None:
    from pipelines.nodes.normalize import normalize_node

    state: GraphState = {  # type: ignore[typeddict-item]
        "doc_type": "passport",
        "extracted_fields": {
            "surname": "SMITH",
            "given_names": "JOHN",
            "passport_number": "AB123456",
            "date_of_expiry": "2030-01-01",
        },
    }
    result = normalize_node(state)
    schema = result["universal_schema"]
    assert schema["holder_name"] == "JOHN SMITH"
    assert schema["id_number"] == "AB123456"
    assert schema["expiry_date"] == "2030-01-01"


def test_router_routes_to_normalize_above_threshold() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "validate_confidence": 0.95,
        "retry_count": 0,
    }
    assert route_after_validate(state) == "normalize"


def test_router_routes_to_retry_when_retries_remain() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "validate_confidence": 0.50,
        "retry_count": 0,
    }
    assert route_after_validate(state) == "op_a_retry"


def test_router_routes_to_hitl_when_retries_exhausted() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "validate_confidence": 0.50,
        "retry_count": 2,  # == MAX_RETRIES
    }
    assert route_after_validate(state) == "op_b_hitl"


def test_build_graph_returns_state_graph() -> None:
    raise NotImplementedError
