from pydantic import BaseModel


class AgentResult(BaseModel):
    success: bool
    confidence: float
    data: dict
    reason: str | None = None
    tool_calls_made: int = 0
