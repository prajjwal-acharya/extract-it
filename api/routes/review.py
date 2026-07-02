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
    from pipelines.graph import graph  # deferred: graph.invoke wiring lands in P7
    config = {"configurable": {"thread_id": document_id}}
    result = graph.invoke(Command(resume=decision.model_dump()), config=config)  # type: ignore[attr-defined]
    return {"status": "resumed", "state": result}
