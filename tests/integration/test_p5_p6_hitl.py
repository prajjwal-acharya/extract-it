"""Integration seam P5↔P6: HITL decision feeds back into the normalize path."""


def test_hitl_approval_routes_to_normalize() -> None:
    """An approved HITL decision results in the normalize node being called."""
    raise NotImplementedError


def test_hitl_rejection_ends_the_graph() -> None:
    """A rejected HITL decision terminates the graph without reaching normalize."""
    raise NotImplementedError


def test_hitl_corrections_are_merged_into_extracted_fields() -> None:
    """Corrections supplied in the HITL payload are merged into extracted_fields."""
    raise NotImplementedError
