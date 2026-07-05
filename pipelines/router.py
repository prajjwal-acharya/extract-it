from config.settings import settings
from pipelines.state import GraphState


def route_after_truth(state: GraphState) -> str:
    """Route after truth_engine_node by reading TruthReport.persistence.document_status.

    Graph nodes must not inspect confidence values or verification reports.
    The PersistenceDecision already encodes the business logic.

    NORMALIZE  — document_status is "completed" (confidence + verifiers passed)
    OP_A_RETRY — not completed, retries remain
    OP_B_HITL  — not completed, retries exhausted
    """
    truth_report = state.get("truth_report")
    if truth_report is None:
        return "op_b_hitl"

    if truth_report.persistence.document_status == "completed":
        return "normalize"
    if state.get("retry_count", 0) < settings.MAX_RETRIES:
        return "op_a_retry"
    return "op_b_hitl"


def route_after_hitl(state: GraphState) -> str:
    """Return the next node name after the HITL node."""
    return "normalize" if state.get("hitl_approved") else "persist"
