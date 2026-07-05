"""Tests for Phase 5.1 — Resolution Engine.

Covers: Strategy, RetryPlan, ExecutionRecord, ResolutionDecision,
        ResolutionPlanner, StrategyExecutor, node wiring, graph topology,
        and regression tests confirming retry behaviour is unchanged.
"""
import unittest.mock as mock

import pytest

from pipelines.resolution.executor import StrategyExecutor
from pipelines.resolution.models import (
    ExecutionRecord,
    ResolutionDecision,
    RetryPlan,
    Strategy,
)
from pipelines.resolution.planner import ResolutionPlanner
from pipelines.truth_engine.models import (
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_truth_report(doc_status: str, final_confidence: float = 0.90) -> TruthReport:
    allow = doc_status == "completed"
    return TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=final_confidence, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[], required_fields_missing=[],
            additional_fields=[], coverage_score=1.0,
        ),
        verification_reports=[],
        final_confidence=final_confidence,
        decision_reason="test",
        persistence=PersistenceDecision(
            document_status=doc_status,
            allow_completion=allow,
            allow_embedding=allow,
            allow_learning=allow,
            reason=f"test_reason_for_{doc_status}",
        ),
    )


# ---------------------------------------------------------------------------
# Strategy enum
# ---------------------------------------------------------------------------


def test_strategy_enum_executable_strategies() -> None:
    assert Strategy.ACCEPT == "accept"
    assert Strategy.RETRY == "retry"
    assert Strategy.HITL == "hitl"
    assert Strategy.REJECT == "reject"


def test_strategy_enum_future_placeholders() -> None:
    assert Strategy.PROMPT_REFINEMENT == "prompt_refinement"
    assert Strategy.BETTER_RETRIEVAL == "better_retrieval"
    assert Strategy.IMAGE_PREPROCESS == "image_preprocess"
    assert Strategy.MODEL_ESCALATION == "model_escalation"


def test_strategy_enum_has_eight_values() -> None:
    assert len(Strategy) == 8


# ---------------------------------------------------------------------------
# RetryPlan
# ---------------------------------------------------------------------------


def test_retry_plan_construction() -> None:
    plan = RetryPlan(
        attempt_number=1,
        reason="low_confidence",
        retrieval_strategy="similarity_search",
        prompt_strategy="standard",
    )
    assert plan.attempt_number == 1
    assert plan.retrieval_strategy == "similarity_search"
    assert plan.prompt_strategy == "standard"


def test_retry_plan_second_attempt() -> None:
    plan = RetryPlan(
        attempt_number=2, reason="still_low", retrieval_strategy="no_context", prompt_strategy="standard"
    )
    assert plan.attempt_number == 2


# ---------------------------------------------------------------------------
# ExecutionRecord
# ---------------------------------------------------------------------------


def test_execution_record_construction() -> None:
    record = ExecutionRecord(
        strategy=Strategy.RETRY,
        timestamp="2026-01-01T00:00:00+00:00",
        outcome="retry_scheduled",
        confidence_before=0.70,
        confidence_after=None,
    )
    assert record.strategy == Strategy.RETRY
    assert record.outcome == "retry_scheduled"
    assert record.confidence_after is None


def test_execution_record_accept_has_confidence_after() -> None:
    record = ExecutionRecord(
        strategy=Strategy.ACCEPT,
        timestamp="2026-01-01T00:00:00+00:00",
        outcome="accepted",
        confidence_before=0.92,
        confidence_after=0.92,
    )
    assert record.confidence_after == pytest.approx(0.92)


# ---------------------------------------------------------------------------
# ResolutionDecision
# ---------------------------------------------------------------------------


def test_resolution_decision_defaults() -> None:
    decision = ResolutionDecision(
        strategy=Strategy.ACCEPT, reason="ok", requires_human=False
    )
    assert decision.retry_plan is None
    assert decision.execution_history == []
    assert decision.learning_candidate is False
    assert decision.schema_proposal is None


def test_resolution_decision_with_retry_plan() -> None:
    plan = RetryPlan(attempt_number=1, reason="low", retrieval_strategy="sim", prompt_strategy="std")
    decision = ResolutionDecision(
        strategy=Strategy.RETRY,
        reason="retry",
        requires_human=False,
        retry_plan=plan,
    )
    assert decision.retry_plan is plan
    assert decision.retry_plan.attempt_number == 1


def test_resolution_decision_accept_learning_candidate() -> None:
    decision = ResolutionDecision(
        strategy=Strategy.ACCEPT, reason="ok", requires_human=False, learning_candidate=True
    )
    assert decision.learning_candidate is True


# ---------------------------------------------------------------------------
# ResolutionPlanner — accept
# ---------------------------------------------------------------------------


def test_planner_accept_for_completed_document() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("completed", 0.92)
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert decision.strategy == Strategy.ACCEPT
    assert decision.requires_human is False


def test_planner_accept_sets_learning_candidate() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("completed")
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert decision.learning_candidate is True


def test_planner_accept_reason_includes_persistence_reason() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("completed")
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert "test_reason_for_completed" in decision.reason


def test_planner_accept_no_retry_plan() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("completed")
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert decision.retry_plan is None


# ---------------------------------------------------------------------------
# ResolutionPlanner — retry
# ---------------------------------------------------------------------------


def test_planner_retry_when_failed_and_retries_remain() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("failed", 0.60)
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert decision.strategy == Strategy.RETRY
    assert decision.requires_human is False


def test_planner_retry_when_verification_failed_and_retries_remain() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("verification_failed")
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert decision.strategy == Strategy.RETRY


def test_planner_retry_populates_retry_plan() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("failed", 0.60)
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert decision.retry_plan is not None
    assert decision.retry_plan.attempt_number == 1
    assert decision.retry_plan.retrieval_strategy == "similarity_search"
    assert decision.retry_plan.prompt_strategy == "standard"


def test_planner_retry_attempt_number_increments_with_retry_count() -> None:
    planner = ResolutionPlanner(max_retries=3)
    report = _make_truth_report("failed")
    d1 = planner.plan(report, retry_count=0, execution_history=[])
    d2 = planner.plan(report, retry_count=1, execution_history=[])
    assert d1.retry_plan.attempt_number == 1
    assert d2.retry_plan.attempt_number == 2


def test_planner_retry_reason_includes_attempt_context() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("failed")
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert "attempt_1" in decision.reason
    assert "2" in decision.reason  # max_retries denominator


# ---------------------------------------------------------------------------
# ResolutionPlanner — HITL
# ---------------------------------------------------------------------------


def test_planner_hitl_when_retries_exhausted() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("failed", 0.60)
    decision = planner.plan(report, retry_count=2, execution_history=[])
    assert decision.strategy == Strategy.HITL
    assert decision.requires_human is True


def test_planner_hitl_when_truth_report_none() -> None:
    planner = ResolutionPlanner(max_retries=2)
    decision = planner.plan(None, retry_count=0, execution_history=[])
    assert decision.strategy == Strategy.HITL
    assert decision.requires_human is True


def test_planner_hitl_reason_includes_exhausted_count() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("failed")
    decision = planner.plan(report, retry_count=2, execution_history=[])
    assert "retries_exhausted" in decision.reason
    assert "2" in decision.reason


def test_planner_hitl_no_retry_plan() -> None:
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("failed")
    decision = planner.plan(report, retry_count=2, execution_history=[])
    assert decision.retry_plan is None


# ---------------------------------------------------------------------------
# StrategyExecutor
# ---------------------------------------------------------------------------


def test_executor_retry_returns_one_record() -> None:
    executor = StrategyExecutor()
    decision = ResolutionDecision(strategy=Strategy.RETRY, reason="r", requires_human=False)
    records = executor.execute(decision, confidence_before=0.70)
    assert len(records) == 1
    assert records[0].strategy == Strategy.RETRY
    assert records[0].outcome == "retry_scheduled"


def test_executor_retry_confidence_after_is_none() -> None:
    executor = StrategyExecutor()
    decision = ResolutionDecision(strategy=Strategy.RETRY, reason="r", requires_human=False)
    records = executor.execute(decision, confidence_before=0.70)
    assert records[0].confidence_after is None
    assert records[0].confidence_before == pytest.approx(0.70)


def test_executor_accept_sets_confidence_after() -> None:
    executor = StrategyExecutor()
    decision = ResolutionDecision(strategy=Strategy.ACCEPT, reason="ok", requires_human=False)
    records = executor.execute(decision, confidence_before=0.92)
    assert records[0].confidence_after == pytest.approx(0.92)
    assert records[0].outcome == "accepted"


def test_executor_hitl_returns_record_with_hitl_required() -> None:
    executor = StrategyExecutor()
    decision = ResolutionDecision(strategy=Strategy.HITL, reason="exhausted", requires_human=True)
    records = executor.execute(decision, confidence_before=0.55)
    assert records[0].outcome == "hitl_required"
    assert records[0].confidence_after is None


def test_executor_reject_returns_rejected_outcome() -> None:
    executor = StrategyExecutor()
    decision = ResolutionDecision(strategy=Strategy.REJECT, reason="r", requires_human=False)
    records = executor.execute(decision, confidence_before=0.30)
    assert records[0].outcome == "rejected"


def test_executor_timestamp_is_iso_string() -> None:
    executor = StrategyExecutor()
    decision = ResolutionDecision(strategy=Strategy.ACCEPT, reason="ok", requires_human=False)
    records = executor.execute(decision, confidence_before=0.90)
    ts = records[0].timestamp
    assert isinstance(ts, str)
    assert "T" in ts  # ISO 8601 format


@pytest.mark.parametrize("strategy", [
    Strategy.PROMPT_REFINEMENT,
    Strategy.BETTER_RETRIEVAL,
    Strategy.IMAGE_PREPROCESS,
    Strategy.MODEL_ESCALATION,
])
def test_executor_unimplemented_strategies_raise_not_implemented(strategy: Strategy) -> None:
    executor = StrategyExecutor()
    decision = ResolutionDecision(strategy=strategy, reason="future", requires_human=False)
    with pytest.raises(NotImplementedError, match="reserved for a future phase"):
        executor.execute(decision, confidence_before=0.80)


# ---------------------------------------------------------------------------
# Node wiring — resolution_planner_node
# ---------------------------------------------------------------------------


def test_resolution_planner_node_returns_resolution_decision() -> None:
    from pipelines.nodes.resolution_planner import resolution_planner_node

    report = _make_truth_report("completed", 0.92)
    state = {
        "truth_report": report,
        "retry_count": 0,
        "execution_history": [],
    }
    result = resolution_planner_node(state)  # type: ignore[arg-type]
    assert "resolution_decision" in result
    assert isinstance(result["resolution_decision"], ResolutionDecision)
    assert result["resolution_decision"].strategy == Strategy.ACCEPT


def test_resolution_planner_node_missing_truth_report_gives_hitl() -> None:
    from pipelines.nodes.resolution_planner import resolution_planner_node

    state = {
        "truth_report": None,
        "retry_count": 0,
        "execution_history": [],
    }
    result = resolution_planner_node(state)  # type: ignore[arg-type]
    assert result["resolution_decision"].strategy == Strategy.HITL


def test_resolution_planner_node_passes_retry_count_to_planner() -> None:
    from pipelines.nodes.resolution_planner import resolution_planner_node

    report = _make_truth_report("failed", 0.50)
    # With retry_count=2 and default MAX_RETRIES=2, should HITL
    state = {
        "truth_report": report,
        "retry_count": 2,
        "execution_history": [],
    }
    result = resolution_planner_node(state)  # type: ignore[arg-type]
    assert result["resolution_decision"].strategy == Strategy.HITL


# ---------------------------------------------------------------------------
# Node wiring — strategy_executor_node
# ---------------------------------------------------------------------------


def test_strategy_executor_node_returns_execution_history_list() -> None:
    from pipelines.nodes.strategy_executor import strategy_executor_node

    decision = ResolutionDecision(strategy=Strategy.ACCEPT, reason="ok", requires_human=False)
    report = _make_truth_report("completed", 0.92)
    state = {
        "resolution_decision": decision,
        "truth_report": report,
    }
    result = strategy_executor_node(state)  # type: ignore[arg-type]
    assert "execution_history" in result
    assert isinstance(result["execution_history"], list)
    assert len(result["execution_history"]) == 1


def test_strategy_executor_node_uses_truth_report_confidence() -> None:
    from pipelines.nodes.strategy_executor import strategy_executor_node

    decision = ResolutionDecision(strategy=Strategy.RETRY, reason="low", requires_human=False)
    report = _make_truth_report("failed", 0.65)
    state = {
        "resolution_decision": decision,
        "truth_report": report,
    }
    result = strategy_executor_node(state)  # type: ignore[arg-type]
    assert result["execution_history"][0].confidence_before == pytest.approx(0.65)


def test_strategy_executor_node_zero_confidence_when_no_truth_report() -> None:
    from pipelines.nodes.strategy_executor import strategy_executor_node

    decision = ResolutionDecision(strategy=Strategy.HITL, reason="no_report", requires_human=True)
    state = {
        "resolution_decision": decision,
        "truth_report": None,
    }
    result = strategy_executor_node(state)  # type: ignore[arg-type]
    assert result["execution_history"][0].confidence_before == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Graph topology
# ---------------------------------------------------------------------------


def test_graph_has_resolution_planner_and_executor() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    nodes = set(g.get_graph().nodes.keys())
    assert "resolution_planner" in nodes
    assert "strategy_executor" in nodes


def test_graph_truth_engine_feeds_resolution_planner() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    edges = g.get_graph().edges
    next_from_truth = {e[1] for e in edges if e[0] == "truth_engine"}
    assert "resolution_planner" in next_from_truth
    # Static routing targets must not appear as direct truth_engine successors
    assert "normalize" not in next_from_truth
    assert "op_a_retry" not in next_from_truth
    assert "op_b_hitl" not in next_from_truth


def test_graph_resolution_planner_feeds_executor() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    edges = g.get_graph().edges
    next_from_planner = {e[1] for e in edges if e[0] == "resolution_planner"}
    assert "strategy_executor" in next_from_planner


def test_graph_executor_has_conditional_edges_to_all_targets() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from pipelines.graph import build_graph

    with mock.patch("pipelines.graph.get_checkpointer", return_value=MemorySaver()):
        g = build_graph()

    edges = g.get_graph().edges
    next_from_executor = {e[1] for e in edges if e[0] == "strategy_executor"}
    assert "normalize" in next_from_executor
    assert "op_a_retry" in next_from_executor
    assert "op_b_hitl" in next_from_executor
    assert "persist" in next_from_executor


def test_graph_route_after_truth_is_removed() -> None:
    """Regression: route_after_truth must not exist — replaced by the Resolution Engine."""
    import pipelines.router as router_mod

    assert not hasattr(router_mod, "route_after_truth"), (
        "route_after_truth was replaced in Phase 5.1 by route_after_executor. "
        "Use ResolutionPlanner + StrategyExecutor instead."
    )


# ---------------------------------------------------------------------------
# ExecutionHistory accumulation
# ---------------------------------------------------------------------------


def test_execution_history_accumulates_across_retry_passes() -> None:
    """execution_history must append (not overwrite) across multiple passes."""
    from pipelines.nodes.resolution_planner import resolution_planner_node
    from pipelines.nodes.strategy_executor import strategy_executor_node

    report_low = _make_truth_report("failed", 0.55)
    report_high = _make_truth_report("completed", 0.92)

    # First pass — planner decides RETRY
    state1 = {
        "truth_report": report_low,
        "retry_count": 0,
        "execution_history": [],
    }
    plan_result1 = resolution_planner_node(state1)  # type: ignore[arg-type]
    exec_result1 = strategy_executor_node({**state1, **plan_result1})  # type: ignore[arg-type]
    history_after_pass1: list = exec_result1["execution_history"]
    assert len(history_after_pass1) == 1
    assert history_after_pass1[0].strategy == Strategy.RETRY

    # Second pass — planner decides ACCEPT (simulating successful retry)
    state2 = {
        "truth_report": report_high,
        "retry_count": 1,
        "execution_history": history_after_pass1,  # accumulated from pass 1
    }
    plan_result2 = resolution_planner_node(state2)  # type: ignore[arg-type]
    exec_result2 = strategy_executor_node({**state2, **plan_result2})  # type: ignore[arg-type]

    new_records = exec_result2["execution_history"]
    assert len(new_records) == 1
    assert new_records[0].strategy == Strategy.ACCEPT

    # Full accumulated history (LangGraph's reducer would concatenate these)
    full_history = history_after_pass1 + new_records
    assert len(full_history) == 2
    assert full_history[0].strategy == Strategy.RETRY
    assert full_history[1].strategy == Strategy.ACCEPT


# ---------------------------------------------------------------------------
# Regression — retry behavior unchanged
# ---------------------------------------------------------------------------


def test_retry_decision_still_routes_to_op_a_retry() -> None:
    """Regression: RETRY strategy must still send control to op_a_retry."""
    from pipelines.router import route_after_executor

    decision = ResolutionDecision(
        strategy=Strategy.RETRY, reason="low_confidence", requires_human=False
    )
    state = {"resolution_decision": decision}
    assert route_after_executor(state) == "op_a_retry"  # type: ignore[arg-type]


def test_retry_plan_always_uses_similarity_search() -> None:
    """Regression: current retry uses similarity_search retrieval (no change from P4)."""
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("failed", 0.60)
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert decision.retry_plan.retrieval_strategy == "similarity_search"


def test_retry_plan_always_uses_standard_prompt() -> None:
    """Regression: prompt_strategy must be 'standard' until prompt refinement is added."""
    planner = ResolutionPlanner(max_retries=2)
    report = _make_truth_report("failed", 0.60)
    decision = planner.plan(report, retry_count=0, execution_history=[])
    assert decision.retry_plan.prompt_strategy == "standard"


def test_planner_does_not_inspect_raw_confidence() -> None:
    """Planner must read document_status, not final_confidence directly."""
    planner = ResolutionPlanner(max_retries=2)
    # Same document_status="completed" → same ACCEPT decision regardless of confidence value
    low_conf_report = _make_truth_report("completed", final_confidence=0.01)
    high_conf_report = _make_truth_report("completed", final_confidence=0.99)
    d_low = planner.plan(low_conf_report, retry_count=0, execution_history=[])
    d_high = planner.plan(high_conf_report, retry_count=0, execution_history=[])
    assert d_low.strategy == Strategy.ACCEPT
    assert d_high.strategy == Strategy.ACCEPT
