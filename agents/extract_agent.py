import json
import logging

from agents.base import AgentResult
from agents.llm_client import generate
from agents.prompt_builder import build_extraction_prompt
from agents.self_consistency import should_vote, vote
from pipelines.truth_engine.models import ExtractionResult

log = logging.getLogger(__name__)


def _parse_envelope(raw: str, doc_type: str) -> tuple[dict, float]:
    """Parse the extraction envelope {fields: {...}, overall_confidence: float}.

    Falls back gracefully when Gemini returns a flat dict instead of the envelope —
    the whole dict is treated as fields and confidence defaults to 0.9.
    """
    envelope = json.loads(raw)
    if not isinstance(envelope, dict):
        raise ValueError("extraction response is not a JSON object")

    fields = envelope.get("fields")
    if isinstance(fields, dict):
        confidence = float(envelope.get("overall_confidence", 0.9))
    else:
        log.warning(
            "event=EnvelopeMissing doc_type=%s — treating flat response as fields", doc_type
        )
        fields = {
            k: v for k, v in envelope.items() if k not in ("overall_confidence", "confidence")
        }
        confidence = float(envelope.get("overall_confidence", envelope.get("confidence", 0.9)))

    return fields, confidence


def _extract_once(
    content: bytes,
    mime_type: str,
    doc_type: str,
    context: str | None = None,
    additional_instructions: str | None = None,
    model_override: str | None = None,
) -> AgentResult:
    """Single open-extraction pass. Returns AgentResult for internal self-consistency use."""
    prompt = build_extraction_prompt(doc_type, context, additional_instructions)
    try:
        raw = generate(prompt, image_bytes=content, mime_type=mime_type, model=model_override)
        fields, confidence = _parse_envelope(raw, doc_type)
    except Exception as e:
        return AgentResult(success=False, confidence=0.0, data={}, reason=str(e))
    return AgentResult(success=True, confidence=confidence, data=fields)


def extract(
    content: bytes,
    mime_type: str,
    doc_type: str,
    context: str | None = None,
    retrieval_metadata: dict | None = None,
    additional_instructions: str | None = None,
    model_override: str | None = None,
) -> ExtractionResult:
    """Extract all meaningful fields from the document.

    Applies self-consistency voting when extraction confidence is borderline.
    additional_instructions appends focused guidance (PROMPT_REFINEMENT) to the base prompt.
    model_override uses a higher-tier model (MODEL_ESCALATION) for this pass only.
    Deterministic verification is NOT performed here — Phase 4 owns that.
    """
    first = _extract_once(
        content, mime_type, doc_type, context, additional_instructions, model_override
    )

    if not first.success:
        return ExtractionResult(
            fields={},
            overall_confidence=0.0,
            context_used=context is not None,
            sample_count=1,
            retrieval_metadata=retrieval_metadata,
            success=False,
            error=first.reason,
        )

    if not should_vote(first.confidence):
        return ExtractionResult(
            fields=first.data,
            overall_confidence=first.confidence,
            context_used=context is not None,
            sample_count=1,
            retrieval_metadata=retrieval_metadata,
        )

    samples = [first] + [
        _extract_once(
            content, mime_type, doc_type, context, additional_instructions, model_override
        )
        for _ in range(2)
    ]
    voted = vote(samples)
    return ExtractionResult(
        fields=voted.data,
        overall_confidence=voted.confidence,
        context_used=context is not None,
        sample_count=len(samples),
        retrieval_metadata=retrieval_metadata,
    )
