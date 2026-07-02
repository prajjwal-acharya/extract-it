"""Integration seam P4↔P5: validation output drives the routing decision."""
from pipelines.router import route_after_validate
from pipelines.state import GraphState


def test_high_confidence_routes_to_normalize() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "validate_confidence": 0.92,
        "retry_count": 0,
    }
    assert route_after_validate(state) == "normalize"


def test_low_confidence_first_attempt_routes_to_retry() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "validate_confidence": 0.40,
        "retry_count": 0,
    }
    assert route_after_validate(state) == "op_a_retry"


def test_low_confidence_exhausted_retries_routes_to_hitl() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "validate_confidence": 0.40,
        "retry_count": 2,  # == MAX_RETRIES (2)
    }
    assert route_after_validate(state) == "op_b_hitl"
