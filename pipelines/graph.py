from langgraph.graph import StateGraph


def build_graph() -> StateGraph:
    """Construct and return the compiled LangGraph pipeline.

    Topology (P1–P7):
        master → [classify ‖ extract] → validate → route →
            normalize | op_a_retry → validate | op_b_hitl → end
    """
    raise NotImplementedError


graph = None  # replaced by build_graph().compile() in P1
