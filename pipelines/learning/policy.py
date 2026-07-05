"""Learning Policy — sole authority on whether a document may update the knowledge base.

LearningPolicy.evaluate() is the only code path that decides:
  - allow_learning        : document/correction may update RAG + embeddings
  - learn_from_document   : clean pipeline acceptance → exemplar for future extraction
  - learn_from_correction : human-corrected document → correction exemplar
  - schema_candidate      : additional fields discovered → eligible for SchemaProposal

Input:
    ResolutionDecision  — what the pipeline ultimately decided
    TruthReport         — full evidence from Truth Engine
    ExecutionHistory    — every strategy attempt for this document
    is_human_correction — True when corrections were supplied in HITL

No LLM calls.  All decisions are deterministic rules applied to typed evidence.
"""
from __future__ import annotations

from dataclasses import dataclass

from pipelines.resolution.models import ExecutionRecord, ResolutionDecision, Strategy
from pipelines.truth_engine.models import TruthReport


@dataclass
class LearningDecision:
    """Typed output of LearningPolicy.evaluate().

    Downstream nodes (output_writer, exemplar store) read these flags verbatim.
    None of them re-derive the business logic — they only execute it.
    """

    allow_learning: bool       # master gate — must be True for any learning to happen
    learn_from_document: bool  # clean autonomous acceptance → add to RAG exemplars
    learn_from_correction: bool  # human-corrected doc → add as correction exemplar
    schema_candidate: bool     # additional fields present → generate SchemaProposal
    reason: str                # human-readable audit string


class LearningPolicy:
    """Deterministic policy engine for knowledge-base update decisions.

    Rules (evaluated in order, all must hold for allow_learning=True):

    1. Truth Engine permits learning
       truth_report.persistence.allow_learning — encodes: no verifier hard failures
       AND confidence >= threshold.

    2. Pipeline accepted the document
       resolution_decision.strategy == ACCEPT.
       HITL with human approval does not override this — the revalidated
       TruthReport from HITL must also produce ACCEPT before learning is allowed.

    3. No verifier hard failures (redundant double-check for consistency)
       any(r.passed is False) → blocked.

    learn_from_document:  allow_learning AND NOT is_human_correction
    learn_from_correction: allow_learning AND is_human_correction
    schema_candidate:      ACCEPT AND no hard failures AND additional_fields non-empty
                           (gated more loosely — schema proposals are proposals, not writes)
    """

    def evaluate(
        self,
        resolution_decision: ResolutionDecision,
        truth_report: TruthReport,
        execution_history: list[ExecutionRecord],
        is_human_correction: bool = False,
    ) -> LearningDecision:
        te_allows = truth_report.persistence.allow_learning
        accepted = resolution_decision.strategy == Strategy.ACCEPT
        hard_failures = [
            r.verifier_name
            for r in truth_report.verification_reports
            if r.passed is False
        ]
        no_hard_failures = not hard_failures

        allow_learning = te_allows and accepted and no_hard_failures

        learn_from_document = allow_learning and not is_human_correction
        learn_from_correction = allow_learning and is_human_correction

        # Schema candidate: clean acceptance with new fields → worth proposing
        schema_candidate = (
            accepted
            and no_hard_failures
            and len(truth_report.field_validation.additional_fields) > 0
        )

        reason = _build_reason(
            te_allows=te_allows,
            accepted=accepted,
            hard_failures=hard_failures,
            allow_learning=allow_learning,
            is_human_correction=is_human_correction,
        )

        return LearningDecision(
            allow_learning=allow_learning,
            learn_from_document=learn_from_document,
            learn_from_correction=learn_from_correction,
            schema_candidate=schema_candidate,
            reason=reason,
        )


def _build_reason(
    te_allows: bool,
    accepted: bool,
    hard_failures: list[str],
    allow_learning: bool,
    is_human_correction: bool,
) -> str:
    if allow_learning:
        return "learn_from_correction" if is_human_correction else "learning_allowed"

    parts: list[str] = []
    if not te_allows:
        parts.append("truth_engine_disallows")
    if not accepted:
        parts.append("strategy_not_accept")
    if hard_failures:
        parts.append(f"verifier_failures:[{','.join(hard_failures)}]")
    return f"blocked:[{','.join(parts)}]" if parts else "blocked"
