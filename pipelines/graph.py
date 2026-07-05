import logging

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from db.checkpointer import get_checkpointer
from db.models import Document
from db.session import session_scope
from io_pipeline.output_writer import write_output
from pipelines.nodes.classify import classify_node
from pipelines.nodes.extract import extract_node
from pipelines.nodes.master import master_node
from pipelines.nodes.normalize import normalize_node
from pipelines.nodes.op_a_retry import op_a_retry_node
from pipelines.nodes.op_b_hitl import op_b_hitl_node
from pipelines.nodes.resolution_planner import resolution_planner_node
from pipelines.nodes.strategy_executor import strategy_executor_node
from pipelines.nodes.truth_engine import truth_engine_node
from pipelines.nodes.unknown_handler import unknown_handler_node
from pipelines.registry import RoutingAction
from pipelines.router import route_after_executor, route_after_hitl
from pipelines.state import GraphState

log = logging.getLogger(__name__)

_PHASE_MAP = {
    "master": "ingested",
    "classify": "classifying",
    "unknown_handler": "routing_failed",
    "extract": "extracting",
    "truth_engine": "evaluating",
    "resolution_planner": "planning",
    "strategy_executor": "executing",
    "op_a_retry": "retrying",
    "op_b_hitl": "awaiting_review",
    "normalize": "normalizing",
    "persist": "finalizing",
}


def _stamp_phase(name: str, fn):
    def wrapped(state: GraphState) -> dict:
        try:
            with session_scope() as session:
                doc = session.get(Document, state["document_id"])
                if doc is not None:
                    doc.current_phase = _PHASE_MAP.get(name, name)
        except Exception:
            log.warning("phase stamp failed for node=%s doc=%s", name, state.get("document_id"))
        return fn(state)

    return wrapped


def _persist_node(state: GraphState) -> dict:
    write_output(state)
    return {}


def _route_after_classify(state: GraphState) -> str:
    """Route based on RoutingPlan.action.

    PROCEED → extract
    UNKNOWN | FAILURE → unknown_handler
    """
    ctx = state.get("classification_context")
    if ctx is not None and ctx.routing_plan.action == RoutingAction.PROCEED:
        return "extract"
    return "unknown_handler"


def build_graph() -> CompiledStateGraph:
    """Construct and return the compiled LangGraph pipeline.

    Topology:
        master → classify →[route]→ extract (PROCEED)
                                  → unknown_handler (UNKNOWN | FAILURE)
        unknown_handler → persist → END

        extract → truth_engine → resolution_planner → strategy_executor
        strategy_executor →[route_after_executor]→ normalize (ACCEPT)
                                                  → op_a_retry (RETRY)
                                                  → op_b_hitl  (HITL)
                                                  → persist     (REJECT)
        op_a_retry → truth_engine  (retries always regenerate evidence)
        op_b_hitl →[route_after_hitl]→ normalize | persist
        normalize → persist → END
    """
    builder = StateGraph(GraphState)

    for name, node in [
        ("master", master_node),
        ("classify", classify_node),
        ("unknown_handler", unknown_handler_node),
        ("extract", extract_node),
        ("truth_engine", truth_engine_node),
        ("resolution_planner", resolution_planner_node),
        ("strategy_executor", strategy_executor_node),
        ("normalize", normalize_node),
        ("op_a_retry", op_a_retry_node),
        ("op_b_hitl", op_b_hitl_node),
        ("persist", _persist_node),
    ]:
        builder.add_node(name, _stamp_phase(name, node))

    builder.set_entry_point("master")
    builder.add_edge("master", "classify")
    builder.add_conditional_edges(
        "classify",
        _route_after_classify,
        {"extract": "extract", "unknown_handler": "unknown_handler"},
    )
    builder.add_edge("unknown_handler", "persist")

    # Resolution Engine pipeline (replaces static route_after_truth)
    builder.add_edge("extract", "truth_engine")
    builder.add_edge("truth_engine", "resolution_planner")
    builder.add_edge("resolution_planner", "strategy_executor")
    builder.add_conditional_edges(
        "strategy_executor",
        route_after_executor,
        {
            "normalize": "normalize",
            "op_a_retry": "op_a_retry",
            "op_b_hitl": "op_b_hitl",
            "persist": "persist",
        },
    )

    # Retry loop: op_a_retry regenerates evidence via truth_engine → full resolution cycle
    builder.add_edge("op_a_retry", "truth_engine")

    builder.add_conditional_edges(
        "op_b_hitl",
        route_after_hitl,
        {"normalize": "normalize", "persist": "persist"},
    )
    builder.add_edge("normalize", "persist")
    builder.add_edge("persist", END)

    return builder.compile(checkpointer=get_checkpointer())


_graph: CompiledStateGraph | None = None


def get_graph() -> CompiledStateGraph:
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
