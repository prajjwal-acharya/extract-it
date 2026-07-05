"""MODEL_ESCALATION strategy — escalate to a higher-tier model for next extraction.

The escalation model is read from settings.GEMINI_ESCALATION_MODEL and set in
state as model_override. op_a_retry_node passes it to extract_agent.extract(),
which forwards it to llm_client.generate(). One escalation is allowed per
document pass; the planner's history check prevents repeated escalation.

All evidence from the current TruthReport is preserved — escalation does not
reset extraction results. After the escalated pass, the truth engine evaluates
the new extraction result and the planner decides the next step.
"""

from __future__ import annotations

from config.settings import settings


class ModelEscalationStrategy:
    """Selects the escalation model and records the transition metadata."""

    def __init__(self, escalation_model: str = "") -> None:
        # Allow explicit override for tests; fall back to settings
        self._model = escalation_model or settings.GEMINI_ESCALATION_MODEL

    def escalate(self) -> tuple[str, dict]:
        """Return (escalation_model_name, metadata_for_execution_record)."""
        metadata = {
            "base_model": settings.GEMINI_MODEL,
            "escalation_model": self._model,
        }
        return self._model, metadata
