from pipelines.state import GraphState


def op_b_hitl_node(state: GraphState) -> dict:
    """Interrupt the graph and surface extracted fields to a human reviewer.

    Uses langgraph.types.interrupt() to pause execution.  Resumes when the
    human decision payload (approved, corrections) is injected via the API.
    """
    raise NotImplementedError
