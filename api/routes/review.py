from fastapi import APIRouter
from langgraph.types import Command
from pydantic import BaseModel

router = APIRouter()


class ReviewDecision(BaseModel):
    approved: bool
    corrections: dict | None = None


@router.post("/{document_id}/decision")
def submit_decision(document_id: str, decision: ReviewDecision) -> dict:
    """Resume an interrupted graph run with a human review decision."""
    from pipelines.graph import get_graph
    config = {"configurable": {"thread_id": document_id}}
    result = get_graph().invoke(Command(resume=decision.model_dump()), config=config)  # type: ignore[call-overload]
    return {"status": "resumed", "state": result}
