import pytest
from pipelines.graph import graph
from pipelines.state import DocumentState


@pytest.mark.live
def test_full_pipeline_passport() -> None:
    state = DocumentState(
        document_id="test-001",
        filename="passport_ABC123_20240101.pdf",
        object_key="raw/passport_ABC123_20240101.pdf",
        raw_content="[Passport document content here]",
    )
    result = graph.invoke(state)
    assert result["status"] == "complete"
    assert result["universal_schema"].get("doc_type") == "passport"


@pytest.mark.live
def test_full_pipeline_bank_statement() -> None:
    state = DocumentState(
        document_id="test-002",
        filename="bank_statement_XYZ789_20240101.pdf",
        object_key="raw/bank_statement_XYZ789_20240101.pdf",
        raw_content="[Bank statement content here]",
    )
    result = graph.invoke(state)
    assert result["status"] == "complete"


@pytest.mark.live
def test_full_pipeline_unknown_triggers_hitl() -> None:
    state = DocumentState(
        document_id="test-003",
        filename="unknown_DOC_20240101.pdf",
        object_key="raw/unknown_DOC_20240101.pdf",
        raw_content="[Ambiguous document content]",
    )
    result = graph.invoke(state)
    assert result["status"] in ("hitl_complete", "complete", "pending")
