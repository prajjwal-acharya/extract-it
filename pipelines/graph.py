from langgraph.graph import StateGraph, END
from pipelines.state import DocumentState
from pipelines.router import route_after_validate, route_after_hitl
from pipelines.nodes.master import master_node
from pipelines.nodes.normalize import normalize_node
from pipelines.nodes.op_a_retry import op_a_retry_node
from pipelines.nodes.op_b_hitl import op_b_hitl_node
from agents.classify_agent import classify
from agents.extract_agent import extract
from agents.validate_agent import validate


def _classify_node(state: DocumentState) -> dict:
    if state.doc_type:
        return {}
    result = classify(state.raw_content)
    return {"doc_type": result.data["doc_type"], "classify_confidence": result.confidence}


def _extract_node(state: DocumentState) -> dict:
    result = extract(state.raw_content, state.doc_type or "")
    return {"extracted_fields": result.data, "extract_confidence": result.confidence}


def _validate_node(state: DocumentState) -> dict:
    result = validate(state.doc_type or "", state.extracted_fields)
    return {
        "validation_issues": result.data.get("issues", []),
        "validate_confidence": result.confidence,
    }


def build_graph() -> StateGraph:
    g = StateGraph(DocumentState)

    g.add_node("master", master_node)
    g.add_node("classify", _classify_node)
    g.add_node("extract", _extract_node)
    g.add_node("validate", _validate_node)
    g.add_node("op_a_retry", op_a_retry_node)
    g.add_node("op_b_hitl", op_b_hitl_node)
    g.add_node("normalize", normalize_node)

    g.set_entry_point("master")
    g.add_edge("master", "classify")
    g.add_edge("classify", "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", route_after_validate, {
        "normalize": "normalize",
        "op_a_retry": "op_a_retry",
        "op_b_hitl": "op_b_hitl",
    })
    g.add_edge("op_a_retry", "validate")
    g.add_conditional_edges("op_b_hitl", route_after_hitl, {
        "normalize": "normalize",
        "end": END,
    })
    g.add_edge("normalize", END)

    return g


graph = build_graph().compile()
