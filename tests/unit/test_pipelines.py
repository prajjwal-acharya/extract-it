import operator
import typing
import unittest.mock as mock

from pipelines.nodes.master import master_node
from pipelines.resolution.models import PlannerBundle, ResolutionDecision, Strategy
from pipelines.resolution.planner import ResolutionPlanner
from pipelines.router import route_after_executor
from pipelines.state import GraphState
from pipelines.truth_engine.models import (
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
    VerificationReport,
)


# ---------------------------------------------------------------------------
# GraphState shape
# ---------------------------------------------------------------------------


def test_graph_state_is_valid_typed_dict() -> None:
    hints = typing.get_type_hints(GraphState)
    assert "document_id" in hints
    assert "doc_type" in hints
    assert "status" in hints
    assert "truth_report" in hints
    assert "extraction_result" in hints


def test_parallel_fields_have_annotated_reducers() -> None:
    hints = typing.get_type_hints(GraphState, include_extras=True)
    assert typing.get_origin(hints["extracted_fields"]) is typing.Annotated
    assert typing.get_origin(hints["validation_issues"]) is typing.Annotated


def test_validation_issues_uses_add_reducer() -> None:
    hints = typing.get_type_hints(GraphState, include_extras=True)
    args = typing.get_args(hints["validation_issues"])
    assert args[1] is operator.add


def test_tool_call_count_uses_add_reducer() -> None:
    hints = typing.get_type_hints(GraphState, include_extras=True)
    assert typing.get_origin(hints["tool_call_count"]) is typing.Annotated
    args = typing.get_args(hints["tool_call_count"])
    import operator as op

    assert args[1] is op.add


# ---------------------------------------------------------------------------
# Master node
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Normalize node
# ---------------------------------------------------------------------------


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


def test_normalize_canonicalizes_expiry_date() -> None:
    from pipelines.nodes.normalize import normalize_node

    cases = [
        ("10/02/2020", "2020-02-10"),
        ("09 JAN 2030", "2030-01-09"),
        ("2025-06-30", "2025-06-30"),
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
        assert result["universal_schema"]["expiry_date"] == expected


def test_normalize_leaves_unparseable_date_unchanged() -> None:
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


# ---------------------------------------------------------------------------
# Routing — ResolutionPlanner + route_after_executor
# ---------------------------------------------------------------------------


def _make_truth_report(
    final_confidence: float = 0.90,
    allow_completion: bool = True,
    verifier_failed: bool = False,
) -> TruthReport:
    extraction = ExtractionResult(
        fields={"surname": "SMITH"}, overall_confidence=final_confidence,
        context_used=False, sample_count=1,
    )
    fvr = FieldValidationReport(
        required_fields_present=[],
        required_fields_missing=[],
        additional_fields=[],
        coverage_score=1.0,
    )
    vr = (
        [VerificationReport(verifier_name="test", passed=False, confidence=0.0)]
        if verifier_failed
        else []
    )
    if verifier_failed:
        doc_status, ac = "verification_failed", False
    elif allow_completion:
        doc_status, ac = "completed", True
    else:
        doc_status, ac = "failed", False
    persistence = PersistenceDecision(
        document_status=doc_status,
        allow_completion=ac,
        allow_embedding=ac,
        allow_learning=ac,
        reason="test",
    )
    return TruthReport(
        extraction=extraction,
        field_validation=fvr,
        verification_reports=vr,
        final_confidence=final_confidence,
        decision_reason="test",
        persistence=persistence,
    )


def _plan(truth_report, retry_count: int = 0, max_retries: int = 2) -> ResolutionDecision:
    planner = ResolutionPlanner(max_retries=max_retries)
    bundle = PlannerBundle(
        truth_report=truth_report,
        execution_history=[],
        retry_count=retry_count,
        remaining_budget=max(0, max_retries - retry_count),
    )
    return planner.plan(bundle)


def test_planner_routes_to_accept_when_completed() -> None:
    decision = _plan(_make_truth_report(allow_completion=True), retry_count=0)
    assert decision.strategy == Strategy.ACCEPT


def test_planner_routes_to_prompt_refinement_when_confidence_low_and_retries_remain() -> None:
    """Phase 5.3: first low-confidence failure → PROMPT_REFINEMENT before generic RETRY."""
    decision = _plan(_make_truth_report(allow_completion=False, final_confidence=0.60), retry_count=0)
    assert decision.strategy == Strategy.PROMPT_REFINEMENT


def test_planner_routes_to_hitl_when_retries_exhausted() -> None:
    decision = _plan(_make_truth_report(allow_completion=False, final_confidence=0.60), retry_count=2, max_retries=2)
    assert decision.strategy == Strategy.HITL


def test_planner_routes_to_hitl_when_truth_report_missing() -> None:
    decision = _plan(None, retry_count=0)
    assert decision.strategy == Strategy.HITL


def test_planner_verification_failure_blocks_accept() -> None:
    """verification_failed status → document is not ACCEPTED regardless of confidence."""
    report = _make_truth_report(final_confidence=0.95, allow_completion=False, verifier_failed=True)
    decision = _plan(report, retry_count=0)
    assert decision.strategy != Strategy.ACCEPT


def test_route_after_executor_accept_normalizes() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "resolution_decision": ResolutionDecision(
            strategy=Strategy.ACCEPT, reason="ok", requires_human=False
        ),
    }
    assert route_after_executor(state) == "normalize"


def test_route_after_executor_retry_goes_to_op_a_retry() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "resolution_decision": ResolutionDecision(
            strategy=Strategy.RETRY, reason="low_confidence", requires_human=False
        ),
    }
    assert route_after_executor(state) == "op_a_retry"


def test_route_after_executor_prompt_refinement_goes_to_op_a_retry() -> None:
    """Phase 5.3: PROMPT_REFINEMENT routes to op_a_retry (same as RETRY)."""
    state: GraphState = {  # type: ignore[typeddict-item]
        "resolution_decision": ResolutionDecision(
            strategy=Strategy.PROMPT_REFINEMENT, reason="refinement scheduled", requires_human=False
        ),
    }
    assert route_after_executor(state) == "op_a_retry"


def test_route_after_executor_hitl_goes_to_op_b_hitl() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "resolution_decision": ResolutionDecision(
            strategy=Strategy.HITL, reason="retries_exhausted", requires_human=True
        ),
    }
    assert route_after_executor(state) == "op_b_hitl"


def test_route_after_executor_reject_goes_to_persist() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "resolution_decision": ResolutionDecision(
            strategy=Strategy.REJECT, reason="rejected", requires_human=False
        ),
    }
    assert route_after_executor(state) == "persist"


def test_route_after_executor_none_decision_goes_to_hitl() -> None:
    state: GraphState = {  # type: ignore[typeddict-item]
        "resolution_decision": None,
    }
    assert route_after_executor(state) == "op_b_hitl"


def test_route_after_hitl_rejection_goes_to_persist() -> None:
    from pipelines.router import route_after_hitl

    rejected: GraphState = {"hitl_approved": False}  # type: ignore[typeddict-item]
    assert route_after_hitl(rejected) == "persist"

    approved: GraphState = {"hitl_approved": True}  # type: ignore[typeddict-item]
    assert route_after_hitl(approved) == "normalize"


# ---------------------------------------------------------------------------
# op_a_retry node
# ---------------------------------------------------------------------------


def test_op_a_retry_increments_retry_count() -> None:
    from pipelines.nodes.op_a_retry import op_a_retry_node

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

    with (
        mock.patch("pipelines.nodes.op_a_retry.embed", return_value=[0.0] * 768),
        mock.patch("pipelines.nodes.op_a_retry.similarity_search", return_value=[]),
        mock.patch("pipelines.nodes.op_a_retry.extract", return_value=fake_result),
        mock.patch("pipelines.nodes.op_a_retry.session_scope"),
    ):
        result = op_a_retry_node(state)

    assert result["retry_count"] == 1


def test_op_a_retry_returns_extraction_result() -> None:
    """op_a_retry must return extraction_result for truth_engine_node to use."""
    from pipelines.nodes.op_a_retry import op_a_retry_node

    state: GraphState = {  # type: ignore[typeddict-item]
        "document_id": "test-id",
        "filename": "passport_P001_20240101.pdf",
        "object_key": "raw/passport_P001_20240101.pdf",
        "doc_type": "passport",
        "raw_bytes": b"%PDF stub",
        "extracted_fields": {},
        "retry_count": 0,
    }
    fake_result = ExtractionResult(
        fields={"surname": "SMITH"}, overall_confidence=0.9, context_used=False, sample_count=1
    )

    with (
        mock.patch("pipelines.nodes.op_a_retry.embed", return_value=[0.0] * 768),
        mock.patch("pipelines.nodes.op_a_retry.similarity_search", return_value=[]),
        mock.patch("pipelines.nodes.op_a_retry.extract", return_value=fake_result),
        mock.patch("pipelines.nodes.op_a_retry.session_scope"),
    ):
        result = op_a_retry_node(state)

    assert "extraction_result" in result
    assert result["extraction_result"] is fake_result


def test_op_a_retry_does_not_call_validate() -> None:
    """Validation is truth_engine_node's responsibility — op_a_retry must not call it."""
    import pipelines.nodes.op_a_retry as retry_mod

    assert not hasattr(retry_mod, "validate")


def test_op_a_retry_uses_similarity_search_context() -> None:
    from db.models import DocumentEmbedding
    from pipelines.nodes.op_a_retry import op_a_retry_node

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

    captured: dict = {}

    def capture_extract(content, mime_type, doc_type, context=None, **kwargs):
        captured["context"] = context
        return fake_result

    with (
        mock.patch("pipelines.nodes.op_a_retry.embed", return_value=[0.0] * 768),
        mock.patch(
            "pipelines.nodes.op_a_retry.similarity_search", return_value=[(mock_row, 0.1)]
        ),
        mock.patch("pipelines.nodes.op_a_retry.extract", side_effect=capture_extract),
        mock.patch("pipelines.nodes.op_a_retry.session_scope"),
    ):
        op_a_retry_node(state)

    assert captured["context"] is not None
    assert '{"surname": "EXAMPLE"}' in captured["context"]


# ---------------------------------------------------------------------------
# extract_node
# ---------------------------------------------------------------------------


def test_extract_node_passes_rag_context_to_extract() -> None:
    from db.models import DocumentEmbedding
    from pipelines.nodes.extract import extract_node

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
        mock.patch(
            "pipelines.nodes.extract.similarity_search", return_value=[(mock_row, 0.1)]
        ),
        mock.patch("pipelines.nodes.extract.extract", side_effect=capture_extract),
        mock.patch("pipelines.nodes.extract.get_session"),
    ):
        result = extract_node(state)

    assert captured["context"] is not None
    assert '{"surname": "EXAMPLE"}' in captured["context"]
    assert result["extracted_fields"] == {"surname": "EXAMPLE"}
    assert result["tool_call_count"] == 0
    assert result["extraction_result"] is fake_result


def test_extract_node_no_context_when_no_similar_docs() -> None:
    from pipelines.nodes.extract import extract_node

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


# ---------------------------------------------------------------------------
# Graph build
# ---------------------------------------------------------------------------


def test_build_graph_returns_state_graph() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph.state import CompiledStateGraph
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    assert isinstance(g, CompiledStateGraph)


def test_graph_has_truth_engine_node() -> None:
    """truth_engine must be a registered node — validate is no longer present."""
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    node_names = set(g.get_graph().nodes.keys())
    assert "truth_engine" in node_names
    assert "validate" not in node_names


def test_graph_has_resolution_engine_nodes() -> None:
    """Phase 5.1: resolution_planner and strategy_executor must be registered."""
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    node_names = set(g.get_graph().nodes.keys())
    assert "resolution_planner" in node_names
    assert "strategy_executor" in node_names


def test_graph_truth_engine_goes_to_resolution_planner() -> None:
    """truth_engine output must feed resolution_planner, not a static router."""
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    edges = g.get_graph().edges
    next_from_truth = {e[1] for e in edges if e[0] == "truth_engine"}
    assert "resolution_planner" in next_from_truth
    assert "normalize" not in next_from_truth
    assert "op_a_retry" not in next_from_truth


def test_graph_op_a_retry_goes_to_truth_engine() -> None:
    """op_a_retry must feed back into truth_engine to regenerate evidence."""
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    edges = g.get_graph().edges
    next_nodes_from_retry = {e[1] for e in edges if e[0] == "op_a_retry"}
    assert "truth_engine" in next_nodes_from_retry
