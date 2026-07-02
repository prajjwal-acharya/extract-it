from pipelines.state import GraphState


def route_after_validate(state: GraphState) -> str:
    """Return the next node name after the validate node.

    Routes to 'normalize' if confidence meets threshold, 'op_a_retry' if
    retries remain, or 'op_b_hitl' when retries are exhausted.
    """
    raise NotImplementedError


def route_after_hitl(state: GraphState) -> str:
    """Return the next node name after the HITL node.

    Routes to 'normalize' if the human approved, otherwise 'end'.
    """
    raise NotImplementedError
