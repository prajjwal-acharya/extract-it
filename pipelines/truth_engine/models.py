from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractionResult:
    """Typed Phase 3 output. Immutable contract between Phase 3 and Phase 4.

    All discovered fields live in `fields` regardless of whether they appear
    in the reference schema.  Phase 4 decides what to do with them.
    """

    fields: dict
    overall_confidence: float
    context_used: bool
    sample_count: int
    retrieval_metadata: dict | None = None
    success: bool = True
    error: str | None = None


@dataclass
class FieldValidationReport:
    """Coverage analysis: which reference schema fields are present vs absent.

    Does not reject documents. Only computes evidence for Phase 4 decisions.
    """

    required_fields_present: list[str]
    required_fields_missing: list[str]
    additional_fields: list[str]
    coverage_score: float  # len(present) / len(required); 1.0 when no required fields

    @classmethod
    def build(cls, extracted_fields: dict, doc_type: str) -> "FieldValidationReport":
        """Compare extracted_fields against the reference schema for doc_type."""
        from config.schema_loader import load_reference_fields

        required, optional = load_reference_fields(doc_type)
        known = set(required) | set(optional)
        present = [f for f in required if f in extracted_fields]
        missing = [f for f in required if f not in extracted_fields]
        additional = [f for f in extracted_fields if f not in known]
        coverage = len(present) / len(required) if required else 1.0
        return cls(
            required_fields_present=present,
            required_fields_missing=missing,
            additional_fields=additional,
            coverage_score=coverage,
        )


@dataclass
class VerificationReport:
    """Outcome of a single deterministic verifier.

    passed=None means the verifier was not attempted (required fields absent).
    """

    verifier_name: str
    passed: bool | None
    confidence: float
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceBundle:
    """All confidence signals bundled for ConfidenceFusionPolicy.fuse().

    Passing a single bundle rather than separate primitives keeps the fuse()
    signature stable as new signals are added in later phases.
    """

    classify_confidence: float
    extraction_confidence: float
    coverage_score: float
    verification_reports: list[VerificationReport]


@dataclass
class PersistencePolicy:
    """Flags that downstream persistence (P6) must obey.

    Computed by the Truth Engine after confidence fusion. Business logic lives
    here; the persistence layer must not re-implement it.
    """

    allow_completion: bool
    allow_embedding: bool
    allow_learning: bool

    @classmethod
    def from_truth(
        cls,
        verification_reports: list[VerificationReport],
        final_confidence: float,
        threshold: float = 0.85,
    ) -> "PersistencePolicy":
        """Derive persistence policy from verification results and confidence.

        Rule: any deterministic failure (passed=False) blocks all persistence.
        Otherwise, allow_completion requires final_confidence >= threshold;
        allow_embedding and allow_learning follow allow_completion.
        """
        hard_fail = any(r.passed is False for r in verification_reports)
        if hard_fail:
            return cls(allow_completion=False, allow_embedding=False, allow_learning=False)
        allow = final_confidence >= threshold
        return cls(allow_completion=allow, allow_embedding=allow, allow_learning=allow)


@dataclass
class TruthReport:
    """Phase 4 evidence object. Explains why final_confidence is what it is.

    TruthReport is never a routing decision — it is evidence that informs one.
    After Phase 4.2, routing and persistence decisions derive from this object.
    """

    extraction: ExtractionResult
    field_validation: FieldValidationReport
    verification_reports: list[VerificationReport]
    final_confidence: float
    decision_reason: str
    persistence: PersistencePolicy = field(
        default_factory=lambda: PersistencePolicy(
            allow_completion=True, allow_embedding=True, allow_learning=True
        )
    )


def status_from_truth_report(
    truth_report: TruthReport | None,
    *,
    error: str | None = None,
    hitl_required: bool = False,
    hitl_approved: bool | None = None,
) -> str:
    """Derive the document's final status string from TruthReport evidence.

    All status computation is centralised here — no node should compute status
    independently from scattered flags.
    """
    if error:
        return "failed"
    if hitl_required and not hitl_approved:
        return "rejected"
    if truth_report is None:
        return "failed"
    hard_fail = any(r.passed is False for r in truth_report.verification_reports)
    if hard_fail:
        return "verification_failed"
    if truth_report.persistence.allow_completion:
        return "completed"
    return "failed"
