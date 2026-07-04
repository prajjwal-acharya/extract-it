import logging

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from db.checkpointer import get_checkpointer
from db.models import Document
from db.session import get_session
from io_pipeline.output_writer import write_output
from pipelines.nodes.classify import classify_node
from pipelines.nodes.extract import extract_node
from pipelines.nodes.master import master_node
from pipelines.nodes.normalize import normalize_node
from pipelines.nodes.op_a_retry import op_a_retry_node
from pipelines.nodes.op_b_hitl import op_b_hitl_node
from pipelines.nodes.validate import validate_node
from pipelines.router import route_after_hitl, route_after_validate
from pipelines.state import GraphState

log = logging.getLogger(__name__)

_PHASE_MAP = {
    "master": "ingested",
    "classify": "classifying",
    "extract": "extracting",
    "validate": "validating",
    "op_a_retry": "retrying",
    "op_b_hitl": "awaiting_review",
    "normalize": "normalizing",
    "persist": "finalizing",
}


def _stamp_phase(name: str, fn):
    def wrapped(state: GraphState) -> dict:
        result = fn(state)
        try:
            session = get_session()
            doc = session.get(Document, state["document_id"])
            if doc is not None:
                doc.current_phase = _PHASE_MAP.get(name, name)
                session.commit()
        except Exception:
            log.warning("phase stamp failed for node=%s doc=%s", name, state.get("document_id"))
        return result

    return wrapped


def _persist_node(state: GraphState) -> dict:
    write_output(state)
    return {}


def build_graph() -> CompiledStateGraph:
    """Construct and return the compiled LangGraph pipeline.

    Topology: master -> classify -> extract -> validate -> route ->
        normalize | op_a_retry -> validate | op_b_hitl -> normalize | persist -> END
    """
    builder = StateGraph(GraphState)

    for name, node in [
        ("master", master_node),
        ("classify", classify_node),
        ("extract", extract_node),
        ("validate", validate_node),
        ("normalize", normalize_node),
        ("op_a_retry", op_a_retry_node),
        ("op_b_hitl", op_b_hitl_node),
        ("persist", _persist_node),
    ]:
        builder.add_node(name, _stamp_phase(name, node))

    builder.set_entry_point("master")
    builder.add_edge("master", "classify")
    builder.add_edge("classify", "extract")
    builder.add_edge("extract", "validate")
    builder.add_conditional_edges(
        "validate",
        route_after_validate,
        {"normalize": "normalize", "op_a_retry": "op_a_retry", "op_b_hitl": "op_b_hitl"},
    )
    builder.add_edge("op_a_retry", "validate")
    builder.add_conditional_edges(
        "op_b_hitl",
        route_after_hitl,
        {"normalize": "normalize", "persist": "persist"},
    )
    builder.add_edge("normalize", "persist")
    builder.add_edge("persist", END)

    return builder.compile(checkpointer=get_checkpointer())


# Lazy singleton — defers postgres connection until first pipeline invocation
# so the module can be imported safely in tests without a live DB.
_graph: CompiledStateGraph | None = None


def get_graph() -> CompiledStateGraph:
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
