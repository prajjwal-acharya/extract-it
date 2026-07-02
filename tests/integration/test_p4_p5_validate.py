"""Integration seam P4↔P5: validation output drives the routing decision."""


def test_high_confidence_routes_to_normalize() -> None:
    """validate_confidence >= threshold routes to the normalize node, skipping HITL."""
    raise NotImplementedError


def test_low_confidence_first_attempt_routes_to_retry() -> None:
    """validate_confidence < threshold with retry_count=0 routes to op_a_retry."""
    raise NotImplementedError


def test_low_confidence_exhausted_retries_routes_to_hitl() -> None:
    """validate_confidence < threshold with retry_count at max routes to op_b_hitl."""
    raise NotImplementedError
