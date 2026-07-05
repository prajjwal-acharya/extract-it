from __future__ import annotations

import datetime
import logging
import time
from dataclasses import dataclass

from agents.base import AgentResult
from config.settings import settings
from pipelines.registry import (
    ConfidencePolicy,
    RegistryEntry,
    RetryPolicy,
    RoutingAction,
    registry,
)

# Re-export so existing imports from this module continue to work.
__all__ = [
    "RoutingAction",
    "RoutingPlan",
    "ClassificationContext",
    "RoutingEngine",
    "make_classification_context",
    "ROUTING_VERSION",
]

log = logging.getLogger(__name__)

# Increment when routing policy semantics change so every persisted plan is traceable
# to the exact policy that produced it.
ROUTING_VERSION = "2.1"


@dataclass(frozen=True)
class RoutingPlan:
    """Complete execution contract produced by RoutingEngine.

    Every field a downstream phase needs is present here.
    No downstream node should consult the registry again.
    """

    action: RoutingAction
    document_type: str
    reference_schema_name: str
    extraction_prompt_key: str
    verifier_profile: tuple[str, ...]
    retry_policy: RetryPolicy
    confidence_policy: ConfidencePolicy
    rag_namespace: str
    confidence: float
    reason: str
    routing_version: str


@dataclass(frozen=True)
class ClassificationContext:
    """Audit envelope wrapping the RoutingPlan and the original classifier output."""

    routing_plan: RoutingPlan
    raw_result: AgentResult
    model_name: str
    timestamp: datetime.datetime


def _plan_from_entry(
    entry: RegistryEntry,
    confidence: float,
    action: RoutingAction,
    reason: str,
) -> RoutingPlan:
    return RoutingPlan(
        action=action,
        document_type=entry.document_type,
        reference_schema_name=entry.reference_schema_name,
        extraction_prompt_key=entry.extraction_prompt_key,
        verifier_profile=entry.verifier_profile,
        retry_policy=entry.retry_policy,
        confidence_policy=entry.confidence_policy,
        rag_namespace=entry.rag_namespace,
        confidence=confidence,
        reason=reason,
        routing_version=ROUTING_VERSION,
    )


def _unknown_plan(confidence: float, reason: str) -> RoutingPlan:
    return _plan_from_entry(registry.get("UNKNOWN"), confidence, RoutingAction.UNKNOWN, reason)


def _failure_plan(reason: str) -> RoutingPlan:
    return _plan_from_entry(
        registry.get("UNKNOWN"), confidence=0.0, action=RoutingAction.FAILURE, reason=reason
    )


class RoutingEngine:
    """Deterministic routing layer between classifier output and pipeline execution.

    Gemini identifies the document; RoutingEngine decides how the pipeline proceeds.
    The graph depends only on RoutingPlan — it never inspects AgentResult directly.

    Decision precedence:
      1. Agent failure         → FAILURE  (Gemini call failed completely)
      2. Unregistered type     → UNKNOWN  (type string not in registry)
      3. Explicit UNKNOWN type → UNKNOWN  (classifier explicitly returned "UNKNOWN")
      4. policy.evaluate()     → PROCEED | UNKNOWN  (per-doc-type confidence band)
    """

    def route(self, result: AgentResult, raw_doc_type: str) -> RoutingPlan:
        t0 = time.monotonic()

        if not result.success:
            plan = _failure_plan(reason=f"agent_failure: {result.reason}")
            self._log(plan, time.monotonic() - t0)
            return plan

        if not registry.exists(raw_doc_type):
            plan = _unknown_plan(
                confidence=result.confidence,
                reason=f"unregistered_type: {raw_doc_type!r}",
            )
            self._log(plan, time.monotonic() - t0)
            return plan

        entry = registry.get(raw_doc_type)

        if raw_doc_type == "UNKNOWN":
            plan = _plan_from_entry(
                entry,
                result.confidence,
                RoutingAction.UNKNOWN,
                reason="classifier_returned_unknown",
            )
            self._log(plan, time.monotonic() - t0)
            return plan

        # Delegate threshold decision to the per-document-type policy.
        action = entry.confidence_policy.evaluate(result.confidence)
        if action == RoutingAction.PROCEED:
            reason = "confidence_above_threshold"
            plan = _plan_from_entry(entry, result.confidence, action, reason)
        else:
            # Below threshold — best guess preserved in reason; doc becomes UNKNOWN.
            plan = _unknown_plan(
                confidence=result.confidence,
                reason=f"confidence_below_threshold: {raw_doc_type!r} ({result.confidence:.3f} < {entry.confidence_policy.proceed_threshold})",
            )

        self._log(plan, time.monotonic() - t0)
        return plan

    @staticmethod
    def _log(plan: RoutingPlan, elapsed: float) -> None:
        log.info(
            "event=RoutingDecision routing_version=%s action=%s doc_type=%s "
            "reference_schema=%s confidence=%.3f reason=%s elapsed=%.4fs",
            plan.routing_version,
            plan.action.value,
            plan.document_type,
            plan.reference_schema_name,
            plan.confidence,
            plan.reason,
            elapsed,
        )


def make_classification_context(plan: RoutingPlan, result: AgentResult) -> ClassificationContext:
    return ClassificationContext(
        routing_plan=plan,
        raw_result=result,
        model_name=settings.GEMINI_MODEL,
        timestamp=datetime.datetime.now(tz=datetime.timezone.utc),
    )
