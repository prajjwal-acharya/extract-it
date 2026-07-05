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
class PersistenceDecision:
    """Computed downstream decision that P6 must execute without re-deriving.

    Truth Engine is the sole authority. Persistence layer reads and obeys;
    it must not re-implement any of the business logic encoded here.

    document_status: canonical document status (completed|verification_failed|failed)
    reason:          human-readable explanation for audit replay
    """

    document_status: str
    allow_completion: bool
    allow_embedding: bool
    allow_learning: bool
    reason: str

    @classmethod
    def from_truth(
        cls,
        verification_reports: list[VerificationReport],
        final_confidence: float,
        threshold: float = 0.85,
    ) -> "PersistenceDecision":
        """Compute the persistence decision from verification results and confidence.

        Rule: any deterministic failure (passed=False) → verification_failed status,
        all flags False. Otherwise, allow_* follows final_confidence >= threshold.
        """
        hard_fail = any(r.passed is False for r in verification_reports)
        if hard_fail:
            failed = [r.verifier_name for r in verification_reports if r.passed is False]
            return cls(
                document_status="verification_failed",
                allow_completion=False,
                allow_embedding=False,
                allow_learning=False,
                reason=f"deterministic_failure:[{','.join(failed)}]",
            )
        allow = final_confidence >= threshold
        return cls(
            document_status="completed" if allow else "failed",
            allow_completion=allow,
            allow_embedding=allow,
            allow_learning=allow,
            reason=(
                f"confidence_above_threshold:{final_confidence:.4f}"
                if allow
                else f"confidence_below_threshold:{final_confidence:.4f}"
            ),
        )


@dataclass
class TruthReport:
    """Phase 4 evidence object. Explains why final_confidence is what it is.

    TruthReport is never a routing decision — it is evidence that informs one.
    Routing, persistence, and status derivation all consume this object.
    verifier_version tags which verifier set produced the evidence (audit replay).
    """

    extraction: ExtractionResult
    field_validation: FieldValidationReport
    verification_reports: list[VerificationReport]
    final_confidence: float
    decision_reason: str
    persistence: PersistenceDecision = field(
        default_factory=lambda: PersistenceDecision(
            document_status="completed",
            allow_completion=True,
            allow_embedding=True,
            allow_learning=True,
            reason="default",
        )
    )
    verifier_version: str = "unknown"
