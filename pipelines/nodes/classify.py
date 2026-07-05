import logging
import time

from agents.classify_agent import classify
from pipelines.routing_engine import RoutingEngine, make_classification_context
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename

log = logging.getLogger(__name__)


def classify_node(state: GraphState) -> dict:
    """Classify document and produce a RoutingPlan via RoutingEngine.

    The graph depends only on classification_context.routing_plan — it never
    inspects the raw AgentResult. doc_type, classify_confidence, and routing_version
    are written from the plan so downstream nodes (extract, validate) need no changes.
    """
    t0 = time.monotonic()
    log.info(
        "event=ClassificationStarted document_id=%s filename=%r",
        state.get("document_id"),
        state.get("filename"),
    )

    mime_type = mime_from_filename(state["filename"])
    result = classify(state["raw_bytes"], mime_type)

    raw_type = result.data.get("doc_type", "") if result.success else ""
    plan = RoutingEngine().route(result, raw_type)
    ctx = make_classification_context(plan, result)

    elapsed = time.monotonic() - t0
    log.info(
        "event=ClassificationComplete document_id=%s routing_version=%s action=%s "
        "doc_type=%s reference_schema=%s confidence=%.3f reason=%s elapsed=%.3fs",
        state.get("document_id"),
        plan.routing_version,
        plan.action.value,
        plan.document_type,
        plan.reference_schema_name,
        plan.confidence,
        plan.reason,
        elapsed,
    )

    return {
        "doc_type": plan.document_type,
        "classify_confidence": plan.confidence,
        "classification_context": ctx,
        "routing_version": plan.routing_version,
    }
