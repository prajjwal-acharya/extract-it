import operator
import typing

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
    state: GraphState = {  # type: ignore[typeddict-item]
        "filename": "bank_statement_ACC001_20240101.pdf",
    }
    result = master_node(state)
    assert result.get("doc_type") == "bank_statement"


def test_master_node_returns_empty_dict_for_unmatched_filename() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "filename": "random_document.pdf",
    }
    result = master_node(state)
    assert result == {}


def test_normalize_node_produces_universal_schema() -> None:
    raise NotImplementedError


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
