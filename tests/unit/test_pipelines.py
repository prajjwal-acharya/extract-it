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


def test_route_after_hitl_rejection_goes_to_persist() -> None:
    from pipelines.router import route_after_hitl

    rejected: GraphState = {"hitl_approved": False}  # type: ignore[typeddict-item]
    assert route_after_hitl(rejected) == "persist"

    approved: GraphState = {"hitl_approved": True}  # type: ignore[typeddict-item]
    assert route_after_hitl(approved) == "normalize"


def test_op_a_retry_increments_retry_count() -> None:
    from pipelines.nodes.op_a_retry import op_a_retry_node
    from pipelines.truth_engine.models import ExtractionResult
    from agents.base import AgentResult

    state: GraphState = {  # type: ignore[typeddict-item]
        "document_id": "test-id",
        "filename": "passport_P001_20240101.pdf",
        "object_key": "raw/passport_P001_20240101.pdf",
        "doc_type": "passport",
        "raw_bytes": b"%PDF stub",
        "extracted_fields": {"surname": "SMITH"},
        "retry_count": 0,
    }
    fake_result = ExtractionResult(
        fields={"surname": "SMITH"}, overall_confidence=0.9, context_used=False, sample_count=1
    )
    fake_validate = AgentResult(success=True, confidence=0.9, data={"issues": []})

    with (
        mock.patch("pipelines.nodes.op_a_retry.embed", return_value=[0.0] * 768),
        mock.patch("pipelines.nodes.op_a_retry.similarity_search", return_value=[]),
        mock.patch("pipelines.nodes.op_a_retry.extract", return_value=fake_result),
        mock.patch("pipelines.nodes.op_a_retry.validate", return_value=fake_validate),
        mock.patch("pipelines.nodes.op_a_retry.session_scope"),
    ):
        result = op_a_retry_node(state)

    assert result["retry_count"] == 1


def test_op_a_retry_uses_similarity_search_context() -> None:
    from db.models import DocumentEmbedding
    from pipelines.nodes.op_a_retry import op_a_retry_node
    from pipelines.truth_engine.models import ExtractionResult
    from agents.base import AgentResult

    state: GraphState = {  # type: ignore[typeddict-item]
        "document_id": "test-id",
        "filename": "passport_P001_20240101.pdf",
        "object_key": "raw/passport_P001_20240101.pdf",
        "doc_type": "passport",
        "raw_bytes": b"%PDF stub",
        "extracted_fields": {},
        "retry_count": 1,
    }

    mock_row = mock.MagicMock(spec=DocumentEmbedding)
    mock_row.chunk_text = '{"surname": "EXAMPLE"}'
    mock_row.document_id = "other-doc-id"
    fake_result = ExtractionResult(
        fields={"surname": "SMITH"}, overall_confidence=0.95, context_used=True, sample_count=1
    )
    fake_validate = AgentResult(success=True, confidence=0.95, data={"issues": []})

    captured: dict = {}

    def capture_extract(content, mime_type, doc_type, context=None, **kwargs):
        captured["context"] = context
        return fake_result

    with (
        mock.patch("pipelines.nodes.op_a_retry.embed", return_value=[0.0] * 768),
        mock.patch("pipelines.nodes.op_a_retry.similarity_search", return_value=[(mock_row, 0.1)]),
        mock.patch("pipelines.nodes.op_a_retry.extract", side_effect=capture_extract),
        mock.patch("pipelines.nodes.op_a_retry.validate", return_value=fake_validate),
        mock.patch("pipelines.nodes.op_a_retry.session_scope"),
    ):
        op_a_retry_node(state)

    assert captured["context"] is not None
    assert '{"surname": "EXAMPLE"}' in captured["context"]


def test_normalize_canonicalizes_expiry_date() -> None:
    """normalize_node converts mixed date formats to ISO 8601 (YYYY-MM-DD)."""
    from pipelines.nodes.normalize import normalize_node

    cases = [
        ("10/02/2020", "2020-02-10"),  # DD/MM/YYYY — dayfirst=True
        ("09 JAN 2030", "2030-01-09"),  # human-readable month
        ("2025-06-30", "2025-06-30"),  # already ISO — pass-through
    ]
    for raw, expected in cases:
        state: GraphState = {  # type: ignore[typeddict-item]
            "doc_type": "passport",
            "extracted_fields": {
                "surname": "SMITH",
                "given_names": "JOHN",
                "passport_number": "AB123456",
                "date_of_expiry": raw,
            },
        }
        result = normalize_node(state)
        assert result["universal_schema"]["expiry_date"] == expected, (
            f"Expected {expected!r} for input {raw!r}, got {result['universal_schema']['expiry_date']!r}"
        )


def test_normalize_leaves_unparseable_date_unchanged() -> None:
    """normalize_node passes through dates it cannot parse rather than dropping them."""
    from pipelines.nodes.normalize import normalize_node

    state: GraphState = {  # type: ignore[typeddict-item]
        "doc_type": "passport",
        "extracted_fields": {
            "surname": "SMITH",
            "given_names": "JOHN",
            "passport_number": "AB123456",
            "date_of_expiry": "not-a-date",
        },
    }
    result = normalize_node(state)
    assert result["universal_schema"]["expiry_date"] == "not-a-date"


def test_build_graph_returns_state_graph() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph.state import CompiledStateGraph
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    assert isinstance(g, CompiledStateGraph)


def test_extract_node_passes_rag_context_to_extract() -> None:
    """extract_node should retrieve similar embeddings and forward context= to extract()."""
    from db.models import DocumentEmbedding
    from pipelines.nodes.extract import extract_node
    from pipelines.truth_engine.models import ExtractionResult

    state: GraphState = {  # type: ignore[typeddict-item]
        "document_id": "test-id",
        "filename": "passport_P001_20240101.pdf",
        "object_key": "raw/passport_P001_20240101.pdf",
        "doc_type": "passport",
        "raw_bytes": b"%PDF stub",
    }

    mock_row = mock.MagicMock(spec=DocumentEmbedding)
    mock_row.chunk_text = '{"surname": "EXAMPLE"}'
    mock_row.document_id = "other-doc-id"
    fake_result = ExtractionResult(
        fields={"surname": "EXAMPLE"}, overall_confidence=0.9, context_used=True, sample_count=1
    )

    captured: dict = {}

    def capture_extract(content, mime_type, doc_type, context=None, **kwargs):
        captured["context"] = context
        return fake_result

    with (
        mock.patch("pipelines.nodes.extract.embed", return_value=[0.0] * 768),
        mock.patch("pipelines.nodes.extract.similarity_search", return_value=[(mock_row, 0.1)]),
        mock.patch("pipelines.nodes.extract.extract", side_effect=capture_extract),
        mock.patch("pipelines.nodes.extract.get_session"),
    ):
        result = extract_node(state)

    assert captured["context"] is not None
    assert '{"surname": "EXAMPLE"}' in captured["context"]
    assert result["extracted_fields"] == {"surname": "EXAMPLE"}
    assert result["tool_call_count"] == 0


def test_extract_node_no_context_when_no_similar_docs() -> None:
    """extract_node should pass context=None when similarity_search returns empty."""
    from pipelines.nodes.extract import extract_node
    from pipelines.truth_engine.models import ExtractionResult

    state: GraphState = {  # type: ignore[typeddict-item]
        "document_id": "test-id",
        "filename": "bank_statement_A001_20240101.pdf",
        "object_key": "raw/bank_statement_A001_20240101.pdf",
        "doc_type": "bank_statement",
        "raw_bytes": b"%PDF stub",
    }

    fake_result = ExtractionResult(
        fields={"balance": 500.0}, overall_confidence=0.8, context_used=False, sample_count=1
    )
    captured: dict = {}

    def capture_extract(content, mime_type, doc_type, context=None, **kwargs):
        captured["context"] = context
        return fake_result

    with (
        mock.patch("pipelines.nodes.extract.embed", return_value=[0.0] * 768),
        mock.patch("pipelines.nodes.extract.similarity_search", return_value=[]),
        mock.patch("pipelines.nodes.extract.extract", side_effect=capture_extract),
        mock.patch("pipelines.nodes.extract.get_session"),
    ):
        extract_node(state)

    assert captured["context"] is None


def test_tool_call_count_uses_add_reducer() -> None:
    import typing

    hints = typing.get_type_hints(GraphState, include_extras=True)
    assert typing.get_origin(hints["tool_call_count"]) is typing.Annotated
    args = typing.get_args(hints["tool_call_count"])
    import operator as op

    assert args[1] is op.add
