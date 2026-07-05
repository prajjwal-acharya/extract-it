from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Strategy(str, Enum):
    """Strategies the Resolution Engine can choose after a TruthReport.

    Only ACCEPT and RETRY are executable in Phase 5.1.
    The remaining strategies are architecture placeholders for future phases.
    """

    ACCEPT = "accept"
    RETRY = "retry"
    HITL = "hitl"
    REJECT = "reject"
    PROMPT_REFINEMENT = "prompt_refinement"   # Phase 5.x — not yet implemented
    BETTER_RETRIEVAL = "better_retrieval"      # Phase 5.x — not yet implemented
    IMAGE_PREPROCESS = "image_preprocess"      # Phase 5.x — not yet implemented
    MODEL_ESCALATION = "model_escalation"      # Phase 5.x — not yet implemented


@dataclass
class RetryPlan:
    """Describes how the next extraction attempt should differ from the last.

    prompt_strategy is always "standard" until prompt refinement is implemented.
    retrieval_strategy drives the RAG lookup behaviour in op_a_retry_node.
    """

    attempt_number: int
    reason: str
    retrieval_strategy: str   # "similarity_search" | "no_context"
    prompt_strategy: str      # "standard" (future: "refined" | "targeted")


@dataclass
class ExecutionRecord:
    """Immutable record of one autonomous pipeline attempt.

    execution_history is a list of these stored in GraphState. The planner
    reads history on every pass so it can adapt based on past outcomes.
    """

    strategy: Strategy
    timestamp: str              # ISO 8601 UTC
    outcome: str                # "accepted" | "retry_scheduled" | "hitl_required" | "rejected"
    confidence_before: float
    confidence_after: float | None   # set only when the outcome is final (ACCEPT)


@dataclass
class ResolutionDecision:
    """The planner's complete, strongly-typed downstream decision.

    Every field that a subsequent node, executor, or persistence layer needs
    lives here. No loose routing flags should appear elsewhere in GraphState.
    """

    strategy: Strategy
    reason: str
    requires_human: bool
    retry_plan: RetryPlan | None = None
    execution_history: list[ExecutionRecord] = field(default_factory=list)
    learning_candidate: bool = False
    schema_proposal: dict | None = None   # future: schema discovery trigger
