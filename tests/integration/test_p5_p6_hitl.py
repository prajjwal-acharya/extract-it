"""Integration seam P5↔P6: HITL decision feeds back into the normalize path."""

import unittest.mock as mock

from pipelines.nodes.op_b_hitl import op_b_hitl_node
from pipelines.state import GraphState


def _make_state(**overrides) -> GraphState:
    base: GraphState = {  # type: ignore[typeddict-item]
        "document_id": "doc-001",
        "doc_type": "passport",
        "extracted_fields": {"surname": "SMITH", "given_names": "JOHN"},
        "validation_issues": ["passport_number: field required"],
    }
    return {**base, **overrides}  # type: ignore[return-value,typeddict-item]


def test_hitl_approval_routes_to_normalize() -> None:
    """An approved HITL decision sets hitl_approved=True."""
    with mock.patch("pipelines.nodes.op_b_hitl.interrupt") as mock_interrupt:
        mock_interrupt.return_value = {"approved": True, "corrections": None}
        result = op_b_hitl_node(_make_state())

    assert result["hitl_approved"] is True
    assert result["hitl_required"] is True


def test_hitl_rejection_ends_the_graph() -> None:
    """A rejected HITL decision sets hitl_approved=False."""
    with mock.patch("pipelines.nodes.op_b_hitl.interrupt") as mock_interrupt:
        mock_interrupt.return_value = {"approved": False, "corrections": None}
        result = op_b_hitl_node(_make_state())

    assert result["hitl_approved"] is False
    assert result["hitl_required"] is True


def test_hitl_corrections_are_merged_into_extracted_fields() -> None:
    """Corrections supplied in the HITL payload are merged into extracted_fields."""
    corrections = {"surname": "JONES", "passport_number": "X1234567"}
    with mock.patch("pipelines.nodes.op_b_hitl.interrupt") as mock_interrupt:
        mock_interrupt.return_value = {"approved": True, "corrections": corrections}
        result = op_b_hitl_node(_make_state())

    assert result["extracted_fields"]["surname"] == "JONES"
    assert result["extracted_fields"]["passport_number"] == "X1234567"
    assert result["extracted_fields"]["given_names"] == "JOHN"  # unchanged original field
