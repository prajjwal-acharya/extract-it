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
    PlannerBundle,
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
    VerificationReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_truth_report(
    doc_status: str,
    final_confidence: float = 0.90,
    required_fields_missing: list | None = None,
) -> TruthReport:
    """Build a TruthReport whose evidence aligns with doc_status.

    "verification_failed" → includes a failed VerificationReport so the
    evidence-driven planner correctly identifies it as a verifier failure.
    "failed"              → use a low final_confidence so the planner retries.
    "completed"           → high confidence, no failures, full coverage.
    """
    allow = doc_status == "completed"
    missing = required_fields_missing or []

    if doc_status == "verification_failed":
        verification_reports = [
            VerificationReport(verifier_name="test_verifier", passed=False, confidence=0.0)
        ]
    else:
        verification_reports = []

    present = [] if missing else []  # no required fields in test schema
    return TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=final_confidence, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=present,
            required_fields_missing=missing,
            additional_fields=[],
            coverage_score=1.0 if not missing else 0.0,
        ),
        verification_reports=verification_reports,
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


def _plan(
    truth_report: TruthReport | None,
    retry_count: int = 0,
    max_retries: int = 2,
    execution_history: list | None = None,
    confidence_threshold: float = 0.85,
) -> ResolutionDecision:
    """Convenience wrapper: build PlannerBundle → call ResolutionPlanner.plan()."""
    planner = ResolutionPlanner(max_retries=max_retries, confidence_threshold=confidence_threshold)
    bundle = PlannerBundle(
        truth_report=truth_report,
        execution_history=execution_history or [],
        retry_count=retry_count,
        remaining_budget=max(0, max_retries - retry_count),
    )
    return planner.plan(bundle)


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
        attempt_number=2,
        reason="still_low",
        retrieval_strategy="no_context",
        prompt_strategy="standard",
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
    decision = ResolutionDecision(strategy=Strategy.ACCEPT, reason="ok", requires_human=False)
    assert decision.retry_plan is None
    assert decision.execution_history == []
    assert decision.learning_candidate is False
    assert decision.schema_proposal is None


def test_resolution_decision_with_retry_plan() -> None:
    plan = RetryPlan(
        attempt_number=1, reason="low", retrieval_strategy="sim", prompt_strategy="std"
    )
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
    report = _make_truth_report("completed", 0.92)
    decision = _plan(report, retry_count=0)
    assert decision.strategy == Strategy.ACCEPT
    assert decision.requires_human is False


def test_planner_accept_sets_learning_candidate() -> None:
    report = _make_truth_report("completed", 0.92)
    decision = _plan(report, retry_count=0)
    assert decision.learning_candidate is True


def test_planner_accept_reason_explains_all_signals() -> None:
    """Accept reason must explain evidence, not pre-computed document_status."""
    report = _make_truth_report("completed", 0.92)
    decision = _plan(report, retry_count=0)
    assert "confidence" in decision.reason
    assert "coverage" in decision.reason


def test_planner_accept_no_retry_plan() -> None:
    report = _make_truth_report("completed", 0.92)
    decision = _plan(report, retry_count=0)
    assert decision.retry_plan is None


# ---------------------------------------------------------------------------
# ResolutionPlanner — retry
# ---------------------------------------------------------------------------


def test_planner_prompt_refinement_on_first_failure() -> None:
    """First failure → PROMPT_REFINEMENT (not RETRY) — refinement tried before generic retry."""
    report = _make_truth_report("failed", 0.60)
    decision = _plan(report, retry_count=0)
    assert decision.strategy == Strategy.PROMPT_REFINEMENT
    assert decision.requires_human is False
    assert decision.retry_plan is not None
    assert decision.retry_plan.prompt_strategy == "refined"
    assert decision.retry_plan.prompt_variant is not None


def test_planner_better_retrieval_after_refinement_tried() -> None:
    """Phase 5.4: after PROMPT_REFINEMENT, planner tries BETTER_RETRIEVAL next."""
    from pipelines.resolution.prompt_refinement import failure_variant

    report = _make_truth_report("failed", 0.60)
    variant = failure_variant(report)
    prior_refinement = ExecutionRecord(
        strategy=Strategy.PROMPT_REFINEMENT,
        timestamp="2024-01-01T00:00:00Z",
        outcome="refinement_scheduled",
        confidence_before=0.60,
        confidence_after=None,
        strategy_metadata={"prompt_variant": variant},
    )
    decision = _plan(report, retry_count=1, execution_history=[prior_refinement])
    # BETTER_RETRIEVAL is next in the autonomous strategy cycle
    assert decision.strategy == Strategy.BETTER_RETRIEVAL
    assert decision.requires_human is False


def test_planner_retry_after_all_autonomous_strategies_tried() -> None:
    """After all 4 autonomous strategies are tried → generic RETRY."""
    from pipelines.resolution.models import Strategy
    from pipelines.resolution.prompt_refinement import failure_variant

    report = _make_truth_report("failed", 0.60)
    variant = failure_variant(report)

    def _rec(strategy, variant_key=None):
        return ExecutionRecord(
            strategy=strategy,
            timestamp="t",
            outcome="scheduled",
            confidence_before=0.60,
            confidence_after=None,
            strategy_metadata={"prompt_variant": variant_key} if variant_key else {},
        )

    history = [
        _rec(Strategy.PROMPT_REFINEMENT, variant),
        _rec(Strategy.BETTER_RETRIEVAL, variant),
        _rec(Strategy.IMAGE_PREPROCESS),
        _rec(Strategy.MODEL_ESCALATION),
    ]
    decision = _plan(report, retry_count=4, max_retries=6, execution_history=history)
    assert decision.strategy == Strategy.RETRY


def test_planner_better_retrieval_after_verification_failed_refinement_tried() -> None:
    """Phase 5.4: PROMPT_REFINEMENT for verifier failure already tried → BETTER_RETRIEVAL next."""
    from pipelines.resolution.prompt_refinement import failure_variant

    report = _make_truth_report("verification_failed")
    variant = failure_variant(report)
    prior = ExecutionRecord(
        strategy=Strategy.PROMPT_REFINEMENT,
        timestamp="2024-01-01T00:00:00Z",
        outcome="refinement_scheduled",
        confidence_before=0.90,
        confidence_after=None,
        strategy_metadata={"prompt_variant": variant},
    )
    decision = _plan(report, retry_count=1, execution_history=[prior])
    assert decision.strategy == Strategy.BETTER_RETRIEVAL


def test_planner_retry_plan_populated() -> None:
    """Retry plan must be populated for both PROMPT_REFINEMENT and RETRY strategies."""
    from pipelines.resolution.prompt_refinement import failure_variant

    report = _make_truth_report("failed", 0.60)
    # First pass → PROMPT_REFINEMENT
    d1 = _plan(report, retry_count=0)
    assert d1.retry_plan is not None
    assert d1.retry_plan.attempt_number == 1
    assert d1.retry_plan.retrieval_strategy == "similarity_search"

    # Second pass (refinement tried) → RETRY
    variant = failure_variant(report)
    prior = ExecutionRecord(
        strategy=Strategy.PROMPT_REFINEMENT,
        timestamp="t",
        outcome="refinement_scheduled",
        confidence_before=0.60,
        confidence_after=None,
        strategy_metadata={"prompt_variant": variant},
    )
    d2 = _plan(report, retry_count=1, execution_history=[prior])
    assert d2.retry_plan is not None
    assert d2.retry_plan.attempt_number == 2
    assert d2.retry_plan.prompt_strategy == "standard"


def test_planner_retry_attempt_number_increments_with_retry_count() -> None:
    report = _make_truth_report("failed", 0.60)  # low confidence → always RETRY
    d1 = _plan(report, retry_count=0, max_retries=3)
    d2 = _plan(report, retry_count=1, max_retries=3)
    assert d1.retry_plan.attempt_number == 1
    assert d2.retry_plan.attempt_number == 2


def test_planner_retry_reason_explains_failure() -> None:
    """Retry reason must name the evidence that triggered the retry."""
    report = _make_truth_report("failed", 0.60)
    decision = _plan(report, retry_count=0)
    # Confidence is the primary failure cause → reason names it
    assert "confidence" in decision.reason.lower() or "threshold" in decision.reason.lower()


# ---------------------------------------------------------------------------
# ResolutionPlanner — HITL
# ---------------------------------------------------------------------------


def test_planner_hitl_when_retries_exhausted() -> None:
    report = _make_truth_report("failed", 0.60)
    decision = _plan(report, retry_count=2, max_retries=2)
    assert decision.strategy == Strategy.HITL
    assert decision.requires_human is True


def test_planner_hitl_when_truth_report_none() -> None:
    decision = _plan(None, retry_count=0)
    assert decision.strategy == Strategy.HITL
    assert decision.requires_human is True


def test_planner_hitl_reason_explains_exhaustion() -> None:
    report = _make_truth_report("failed", 0.60)
    decision = _plan(report, retry_count=2, max_retries=2)
    assert "budget" in decision.reason.lower() or "exhausted" in decision.reason.lower()
    assert "2" in decision.reason  # attempt count mentioned


def test_planner_hitl_no_retry_plan() -> None:
    report = _make_truth_report("failed", 0.60)
    decision = _plan(report, retry_count=2, max_retries=2)
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


@pytest.mark.parametrize(
    "strategy",
    [
        Strategy.BETTER_RETRIEVAL,
        Strategy.IMAGE_PREPROCESS,
        Strategy.MODEL_ESCALATION,
    ],
)
def test_executor_phase54_strategies_return_record(strategy: Strategy) -> None:
    """Phase 5.4: all autonomous strategies are implemented and return ExecutionRecords."""
    executor = StrategyExecutor()
    decision = ResolutionDecision(strategy=strategy, reason="autonomous", requires_human=False)
    records = executor.execute(decision, confidence_before=0.80)
    assert len(records) == 1
    assert records[0].strategy == strategy


def test_executor_prompt_refinement_returns_record() -> None:
    """PROMPT_REFINEMENT is implemented — executor records refinement_scheduled outcome."""
    executor = StrategyExecutor()
    plan = RetryPlan(
        attempt_number=1,
        reason="low_confidence",
        retrieval_strategy="similarity_search",
        prompt_strategy="refined",
        prompt_variant="low_confidence",
        refinement_reason="Extraction confidence below threshold.",
    )
    decision = ResolutionDecision(
        strategy=Strategy.PROMPT_REFINEMENT,
        reason="Prompt refinement scheduled.",
        requires_human=False,
        retry_plan=plan,
    )
    records = executor.execute(decision, confidence_before=0.70)
    assert len(records) == 1
    assert records[0].outcome == "refinement_scheduled"
    assert records[0].strategy_metadata["prompt_strategy"] == "refined"
    assert records[0].strategy_metadata["prompt_variant"] == "low_confidence"


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
    """execution_history must append (not overwrite) across multiple passes.

    Phase 5.4 cycle: failure → PROMPT_REFINEMENT → BETTER_RETRIEVAL → ACCEPT.
    Each pass appends exactly one record.
    """
    from pipelines.nodes.resolution_planner import resolution_planner_node
    from pipelines.nodes.strategy_executor import strategy_executor_node

    report_low = _make_truth_report("failed", 0.55)
    report_high = _make_truth_report("completed", 0.92)

    # Pass 1 — planner decides PROMPT_REFINEMENT (first failure, no history)
    state1 = {
        "truth_report": report_low,
        "retry_count": 0,
        "execution_history": [],
    }
    plan_result1 = resolution_planner_node(state1)  # type: ignore[arg-type]
    exec_result1 = strategy_executor_node({**state1, **plan_result1})  # type: ignore[arg-type]
    history_pass1: list = exec_result1["execution_history"]
    assert len(history_pass1) == 1
    assert history_pass1[0].strategy == Strategy.PROMPT_REFINEMENT

    # Pass 2 — PROMPT_REFINEMENT tried for this variant → BETTER_RETRIEVAL
    state2 = {
        "truth_report": report_low,
        "retry_count": 1,
        "execution_history": history_pass1,
    }
    plan_result2 = resolution_planner_node(state2)  # type: ignore[arg-type]
    exec_result2 = strategy_executor_node({**state2, **plan_result2})  # type: ignore[arg-type]
    history_pass2: list = exec_result2["execution_history"]
    assert len(history_pass2) == 1
    assert history_pass2[0].strategy == Strategy.BETTER_RETRIEVAL

    # Pass 3 — extraction succeeds → ACCEPT (regardless of remaining budget)
    accumulated = history_pass1 + history_pass2  # LangGraph Annotated reducer does this
    state3 = {
        "truth_report": report_high,
        "retry_count": 2,
        "execution_history": accumulated,
    }
    plan_result3 = resolution_planner_node(state3)  # type: ignore[arg-type]
    exec_result3 = strategy_executor_node({**state3, **plan_result3})  # type: ignore[arg-type]
    history_pass3: list = exec_result3["execution_history"]
    assert len(history_pass3) == 1
    assert history_pass3[0].strategy == Strategy.ACCEPT

    # Full accumulated history has all three records
    full = accumulated + history_pass3
    assert len(full) == 3
    assert full[0].strategy == Strategy.PROMPT_REFINEMENT
    assert full[1].strategy == Strategy.BETTER_RETRIEVAL
    assert full[2].strategy == Strategy.ACCEPT


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
    """Regression: both PROMPT_REFINEMENT and RETRY use similarity_search retrieval."""
    from pipelines.resolution.prompt_refinement import failure_variant

    report = _make_truth_report("failed", 0.60)
    # First pass → PROMPT_REFINEMENT
    d1 = _plan(report, retry_count=0)
    assert d1.retry_plan.retrieval_strategy == "similarity_search"

    # Second pass → RETRY (refinement tried)
    variant = failure_variant(report)
    prior = ExecutionRecord(
        strategy=Strategy.PROMPT_REFINEMENT,
        timestamp="t",
        outcome="refinement_scheduled",
        confidence_before=0.60,
        confidence_after=None,
        strategy_metadata={"prompt_variant": variant},
    )
    d2 = _plan(report, retry_count=1, execution_history=[prior])
    assert d2.retry_plan.retrieval_strategy == "similarity_search"


def test_retry_strategy_uses_standard_prompt_after_all_variant_strategies_tried() -> None:
    """After PROMPT_REFINEMENT + BETTER_RETRIEVAL + IMAGE_PREPROCESS + MODEL_ESCALATION,
    planner falls to RETRY with 'standard' prompt_strategy."""
    from pipelines.resolution.prompt_refinement import failure_variant

    report = _make_truth_report("failed", 0.60)
    variant = failure_variant(report)

    def _rec(strategy, variant_key=None):
        return ExecutionRecord(
            strategy=strategy,
            timestamp="t",
            outcome="scheduled",
            confidence_before=0.60,
            confidence_after=None,
            strategy_metadata={"prompt_variant": variant_key} if variant_key else {},
        )

    history = [
        _rec(Strategy.PROMPT_REFINEMENT, variant),
        _rec(Strategy.BETTER_RETRIEVAL, variant),
        _rec(Strategy.IMAGE_PREPROCESS),
        _rec(Strategy.MODEL_ESCALATION),
    ]
    decision = _plan(report, retry_count=4, max_retries=8, execution_history=history)
    assert decision.strategy == Strategy.RETRY
    assert decision.retry_plan.prompt_strategy == "standard"


def test_prompt_refinement_uses_refined_prompt_strategy() -> None:
    """PROMPT_REFINEMENT must set prompt_strategy='refined' so op_a_retry uses guidance."""
    report = _make_truth_report("failed", 0.60)
    decision = _plan(report, retry_count=0)
    assert decision.strategy == Strategy.PROMPT_REFINEMENT
    assert decision.retry_plan.prompt_strategy == "refined"


def test_planner_reads_evidence_not_document_status() -> None:
    """Phase 5.2/5.3: planner reads TruthReport evidence, NOT the pre-computed document_status.

    A TruthReport whose document_status claims 'completed' but whose raw evidence
    (low confidence) contradicts it must trigger PROMPT_REFINEMENT (first pass)
    or RETRY (after refinement tried) — never ACCEPT.
    """
    report = TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=0.40, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[],
            required_fields_missing=[],
            additional_fields=[],
            coverage_score=1.0,
        ),
        verification_reports=[],
        final_confidence=0.40,  # well below 0.85 threshold
        decision_reason="test",
        persistence=PersistenceDecision(
            document_status="completed",  # contradicts the evidence
            allow_completion=True,
            allow_embedding=True,
            allow_learning=True,
            reason="test",
        ),
    )
    # Evidence-driven planner: confidence=0.40 < 0.85 → PROMPT_REFINEMENT (ignores document_status)
    decision = _plan(report, retry_count=0)
    assert decision.strategy in (Strategy.PROMPT_REFINEMENT, Strategy.RETRY)
    assert decision.strategy != Strategy.ACCEPT


def test_planner_accepts_despite_failing_document_status_when_evidence_is_green() -> None:
    """Reverse: document_status='failed' but evidence all green → planner ACCEPTs."""
    report = TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=0.95, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[],
            required_fields_missing=[],
            additional_fields=[],
            coverage_score=1.0,
        ),
        verification_reports=[],
        final_confidence=0.95,  # above 0.85
        decision_reason="test",
        persistence=PersistenceDecision(
            document_status="failed",  # contradicts the evidence
            allow_completion=False,
            allow_embedding=False,
            allow_learning=False,
            reason="test",
        ),
    )
    decision = _plan(report, retry_count=0)
    assert decision.strategy == Strategy.ACCEPT  # evidence wins
