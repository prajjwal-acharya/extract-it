from config.settings import settings
from pipelines.state import DocumentState

MAX_RETRIES = 3


def route_after_validate(state: DocumentState) -> str:
    if state.validate_confidence >= settings.CONFIDENCE_THRESHOLD:
        return "normalize"
    if state.retry_count < MAX_RETRIES:
        return "op_a_retry"
    return "op_b_hitl"


def route_after_hitl(state: DocumentState) -> str:
    if state.hitl_approved:
        return "normalize"
    return "end"
