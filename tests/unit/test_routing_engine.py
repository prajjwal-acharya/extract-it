"""Regression tests for RoutingEngine, RoutingPlan, and ClassificationContext — P2B Hardened."""

import datetime
import unittest.mock as mock

import pytest

from agents.base import AgentResult
from pipelines.registry import ConfidencePolicy, RetryPolicy, RoutingAction, registry
from pipelines.routing_engine import (
    ROUTING_VERSION,
    ClassificationContext,
    RoutingPlan,
    RoutingEngine,
    make_classification_context,
)
from pipelines.state import GraphState


# ---------------------------------------------------------------------------
# RoutingPlan — construction and invariants
# ---------------------------------------------------------------------------


def _make_plan(**kwargs) -> RoutingPlan:
    entry = registry.get("passport")
    defaults = dict(
        action=RoutingAction.PROCEED,
        document_type="passport",
        schema_name="passport",
        extraction_prompt_key="passport",
        verifier_profile=entry.verifier_profile,
        retry_policy=entry.retry_policy,
        confidence_policy=entry.confidence_policy,
        rag_namespace="passport",
        confidence=0.95,
        reason="test",
        routing_version=ROUTING_VERSION,
    )
    defaults.update(kwargs)
    return RoutingPlan(**defaults)


def test_routing_plan_is_frozen() -> None:
    plan = _make_plan()
    with pytest.raises(Exception):
        plan.action = RoutingAction.UNKNOWN  # type: ignore[misc]


def test_routing_plan_carries_full_registry_fields() -> None:
    entry = registry.get("passport")
    plan = _make_plan()
    assert plan.schema_name == entry.schema_name
    assert plan.extraction_prompt_key == entry.extraction_prompt_key
    assert plan.rag_namespace == entry.rag_namespace
    assert plan.verifier_profile == entry.verifier_profile
    assert isinstance(plan.retry_policy, RetryPolicy)
    assert isinstance(plan.confidence_policy, ConfidencePolicy)


def test_routing_plan_carries_routing_version() -> None:
    plan = _make_plan()
    assert plan.routing_version == ROUTING_VERSION


def test_all_supported_types_produce_complete_plan() -> None:
    """Every registered doc type should produce a plan with all required fields populated."""
    for entry in registry.all():
        if entry.document_type == "UNKNOWN":
            continue
        result = AgentResult(success=True, confidence=0.95, data={"doc_type": entry.document_type})
        plan = RoutingEngine().route(result, entry.document_type)
        assert plan.document_type == entry.document_type
        assert plan.schema_name == entry.schema_name
        assert plan.extraction_prompt_key == entry.extraction_prompt_key
        assert plan.rag_namespace == entry.rag_namespace
        assert plan.routing_version == ROUTING_VERSION


# ---------------------------------------------------------------------------
# ClassificationContext — construction
# ---------------------------------------------------------------------------


def test_classification_context_wraps_plan_and_raw_result() -> None:
    plan = _make_plan()
    result = AgentResult(success=True, confidence=0.95, data={"doc_type": "passport"})
    ctx = make_classification_context(plan, result)
    assert isinstance(ctx, ClassificationContext)
    assert ctx.routing_plan is plan
    assert ctx.raw_result is result
    assert isinstance(ctx.timestamp, datetime.datetime)
    assert ctx.timestamp.tzinfo is not None


def test_classification_context_is_frozen() -> None:
    plan = _make_plan()
    result = AgentResult(success=True, confidence=0.9, data={})
    ctx = make_classification_context(plan, result)
    with pytest.raises(Exception):
        ctx.routing_plan = plan  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ConfidencePolicy.evaluate() — policy-driven routing
# ---------------------------------------------------------------------------


def test_policy_evaluate_proceeds_above_threshold() -> None:
    policy = ConfidencePolicy(proceed_threshold=0.70)
    assert policy.evaluate(0.70) == RoutingAction.PROCEED
    assert policy.evaluate(0.90) == RoutingAction.PROCEED
    assert policy.evaluate(1.00) == RoutingAction.PROCEED


def test_policy_evaluate_unknown_below_threshold() -> None:
    policy = ConfidencePolicy(proceed_threshold=0.70)
    assert policy.evaluate(0.69) == RoutingAction.UNKNOWN
    assert policy.evaluate(0.00) == RoutingAction.UNKNOWN


def test_policy_evaluate_at_exact_boundary() -> None:
    policy = ConfidencePolicy(proceed_threshold=0.70)
    assert policy.evaluate(0.70) == RoutingAction.PROCEED


def test_per_doc_type_policy_is_independent() -> None:
    """Each registry entry owns its own policy — changing one doesn't affect others."""
    for entry in registry.all():
        if entry.document_type == "UNKNOWN":
            continue
        result = entry.confidence_policy.evaluate(entry.confidence_policy.proceed_threshold)
        assert result == RoutingAction.PROCEED


# ---------------------------------------------------------------------------
# RoutingEngine — PROCEED path
# ---------------------------------------------------------------------------


def test_engine_proceeds_above_threshold() -> None:
    result = AgentResult(success=True, confidence=0.95, data={"doc_type": "passport"})
    plan = RoutingEngine().route(result, "passport")
    assert plan.action == RoutingAction.PROCEED
    assert plan.document_type == "passport"
    assert plan.confidence == 0.95


def test_engine_proceeds_at_exact_threshold() -> None:
    threshold = registry.get("passport").confidence_policy.proceed_threshold
    result = AgentResult(success=True, confidence=threshold, data={"doc_type": "passport"})
    plan = RoutingEngine().route(result, "passport")
    assert plan.action == RoutingAction.PROCEED


def test_engine_medium_confidence_proceeds() -> None:
    """Medium confidence (was RECLASSIFY) now PROCEEDs — validation handles adaptive retry."""
    result = AgentResult(success=True, confidence=0.75, data={"doc_type": "passport"})
    plan = RoutingEngine().route(result, "passport")
    assert plan.action == RoutingAction.PROCEED


# ---------------------------------------------------------------------------
# RoutingEngine — UNKNOWN path
# ---------------------------------------------------------------------------


def test_engine_unknown_below_threshold() -> None:
    result = AgentResult(success=True, confidence=0.30, data={"doc_type": "passport"})
    plan = RoutingEngine().route(result, "passport")
    assert plan.action == RoutingAction.UNKNOWN
    assert plan.document_type == "UNKNOWN"
    assert "passport" in plan.reason  # best guess preserved in reason


def test_engine_unknown_for_unregistered_type() -> None:
    result = AgentResult(success=True, confidence=0.90, data={"doc_type": "birth_certificate"})
    plan = RoutingEngine().route(result, "birth_certificate")
    assert plan.action == RoutingAction.UNKNOWN
    assert "unregistered_type" in plan.reason


def test_engine_unknown_when_classifier_returns_unknown() -> None:
    result = AgentResult(success=True, confidence=0.90, data={"doc_type": "UNKNOWN"})
    plan = RoutingEngine().route(result, "UNKNOWN")
    assert plan.action == RoutingAction.UNKNOWN
    assert "classifier_returned_unknown" in plan.reason


def test_engine_unknown_has_full_routing_version() -> None:
    result = AgentResult(success=True, confidence=0.30, data={"doc_type": "passport"})
    plan = RoutingEngine().route(result, "passport")
    assert plan.routing_version == ROUTING_VERSION


# ---------------------------------------------------------------------------
# RoutingEngine — FAILURE path (agent failure)
# ---------------------------------------------------------------------------


def test_engine_failure_on_agent_failure() -> None:
    result = AgentResult(success=False, confidence=0.0, data={}, reason="llm_timeout")
    plan = RoutingEngine().route(result, "")
    assert plan.action == RoutingAction.FAILURE
    assert "agent_failure" in plan.reason
    assert "llm_timeout" in plan.reason


def test_engine_failure_has_zero_confidence() -> None:
    result = AgentResult(success=False, confidence=0.0, data={}, reason="network_error")
    plan = RoutingEngine().route(result, "")
    assert plan.confidence == 0.0


def test_engine_failure_document_type_is_unknown() -> None:
    result = AgentResult(success=False, confidence=0.0, data={}, reason="timeout")
    plan = RoutingEngine().route(result, "")
    assert plan.document_type == "UNKNOWN"


# ---------------------------------------------------------------------------
# No downstream registry lookups — RoutingPlan is the sole contract
# ---------------------------------------------------------------------------


def test_routing_plan_contains_all_fields_needed_for_extraction() -> None:
    """All fields extract_node needs are in RoutingPlan — no registry lookup required."""
    result = AgentResult(success=True, confidence=0.9, data={"doc_type": "passport"})
    plan = RoutingEngine().route(result, "passport")
    assert plan.schema_name  # used by schema_loader
    assert plan.extraction_prompt_key  # used by future prompt-versioning
    assert plan.rag_namespace  # used by vector retrieval
    assert plan.verifier_profile is not None  # used by verifier pass
    assert plan.retry_policy is not None  # used by Phase 4 retry logic


# ---------------------------------------------------------------------------
# classify_node integration
# ---------------------------------------------------------------------------


def test_classify_node_sets_routing_version_in_state() -> None:
    from pipelines.nodes.classify import classify_node

    state: GraphState = {"filename": "passport_P001_20240101.pdf", "raw_bytes": b"%PDF", "document_id": "d1"}  # type: ignore[typeddict-item]
    fake = AgentResult(success=True, confidence=0.97, data={"doc_type": "passport"})
    with mock.patch("pipelines.nodes.classify.classify", return_value=fake):
        result = classify_node(state)
    assert result["routing_version"] == ROUTING_VERSION
    assert result["classification_context"].routing_plan.action == RoutingAction.PROCEED


def test_classify_node_low_confidence_produces_unknown() -> None:
    from pipelines.nodes.classify import classify_node

    state: GraphState = {"filename": "passport_P001_20240101.pdf", "raw_bytes": b"%PDF", "document_id": "d1"}  # type: ignore[typeddict-item]
    fake = AgentResult(success=True, confidence=0.30, data={"doc_type": "passport"})
    with mock.patch("pipelines.nodes.classify.classify", return_value=fake):
        result = classify_node(state)
    ctx = result["classification_context"]
    assert ctx.routing_plan.action == RoutingAction.UNKNOWN
    assert result["doc_type"] == "UNKNOWN"
    # Best guess accessible via raw_result — not lost
    assert ctx.raw_result.data.get("doc_type") == "passport"


def test_classify_node_agent_failure_produces_failure_action() -> None:
    from pipelines.nodes.classify import classify_node

    state: GraphState = {"filename": "passport_P001_20240101.pdf", "raw_bytes": b"%PDF", "document_id": "d1"}  # type: ignore[typeddict-item]
    fake = AgentResult(success=False, confidence=0.0, data={}, reason="connection_refused")
    with mock.patch("pipelines.nodes.classify.classify", return_value=fake):
        result = classify_node(state)
    assert result["classification_context"].routing_plan.action == RoutingAction.FAILURE


# ---------------------------------------------------------------------------
# Graph routing function — consumes RoutingPlan only
# ---------------------------------------------------------------------------


def test_graph_route_proceeds_to_extract() -> None:
    from pipelines.graph import _route_after_classify

    plan = _make_plan(action=RoutingAction.PROCEED)
    result = AgentResult(success=True, confidence=0.95, data={})
    ctx = make_classification_context(plan, result)
    state: GraphState = {"classification_context": ctx}  # type: ignore[typeddict-item]
    assert _route_after_classify(state) == "extract"


def test_graph_route_unknown_to_unknown_handler() -> None:
    from pipelines.graph import _route_after_classify

    entry = registry.get("UNKNOWN")
    plan = RoutingPlan(
        action=RoutingAction.UNKNOWN,
        document_type="UNKNOWN",
        schema_name=entry.schema_name,
        extraction_prompt_key=entry.extraction_prompt_key,
        verifier_profile=entry.verifier_profile,
        retry_policy=entry.retry_policy,
        confidence_policy=entry.confidence_policy,
        rag_namespace=entry.rag_namespace,
        confidence=0.4,
        reason="test",
        routing_version=ROUTING_VERSION,
    )
    result = AgentResult(success=True, confidence=0.4, data={})
    ctx = make_classification_context(plan, result)
    state: GraphState = {"classification_context": ctx}  # type: ignore[typeddict-item]
    assert _route_after_classify(state) == "unknown_handler"


def test_graph_route_failure_to_unknown_handler() -> None:
    from pipelines.graph import _route_after_classify

    entry = registry.get("UNKNOWN")
    plan = RoutingPlan(
        action=RoutingAction.FAILURE,
        document_type="UNKNOWN",
        schema_name=entry.schema_name,
        extraction_prompt_key=entry.extraction_prompt_key,
        verifier_profile=entry.verifier_profile,
        retry_policy=entry.retry_policy,
        confidence_policy=entry.confidence_policy,
        rag_namespace=entry.rag_namespace,
        confidence=0.0,
        reason="agent_failure: timeout",
        routing_version=ROUTING_VERSION,
    )
    result = AgentResult(success=False, confidence=0.0, data={}, reason="timeout")
    ctx = make_classification_context(plan, result)
    state: GraphState = {"classification_context": ctx}  # type: ignore[typeddict-item]
    assert _route_after_classify(state) == "unknown_handler"


def test_graph_route_missing_context_to_unknown_handler() -> None:
    from pipelines.graph import _route_after_classify

    state: GraphState = {"classification_context": None}  # type: ignore[typeddict-item]
    assert _route_after_classify(state) == "unknown_handler"


# ---------------------------------------------------------------------------
# unknown_handler_node — UNKNOWN termination
# ---------------------------------------------------------------------------


def test_unknown_handler_sets_structured_error() -> None:
    from pipelines.nodes.unknown_handler import unknown_handler_node

    plan = _make_plan(action=RoutingAction.UNKNOWN, reason="confidence_below_threshold: 'passport' (0.300 < 0.70)")
    result = AgentResult(success=True, confidence=0.3, data={})
    ctx = make_classification_context(plan, result)
    state: GraphState = {"document_id": "test-doc", "doc_type": "UNKNOWN", "classification_context": ctx}  # type: ignore[typeddict-item]

    update = unknown_handler_node(state)
    assert "error" in update
    assert "routing_failed" in update["error"]
    assert "confidence_below_threshold" in update["error"]


def test_unknown_handler_handles_failure_action() -> None:
    from pipelines.nodes.unknown_handler import unknown_handler_node

    plan = _make_plan(action=RoutingAction.FAILURE, reason="agent_failure: timeout", confidence=0.0)
    result = AgentResult(success=False, confidence=0.0, data={}, reason="timeout")
    ctx = make_classification_context(plan, result)
    state: GraphState = {"document_id": "test-doc", "doc_type": "UNKNOWN", "classification_context": ctx}  # type: ignore[typeddict-item]

    update = unknown_handler_node(state)
    assert "routing_failed" in update["error"]
    assert "agent_failure" in update["error"]


def test_unknown_handler_handles_missing_context() -> None:
    from pipelines.nodes.unknown_handler import unknown_handler_node

    state: GraphState = {"document_id": "test-doc", "doc_type": None, "classification_context": None}  # type: ignore[typeddict-item]
    update = unknown_handler_node(state)
    assert "routing_failed" in update["error"]
    assert "no_classification_context" in update["error"]
