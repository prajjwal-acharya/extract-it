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

    passed=None means the verifier was not attempted (doc type has no verifier,
    or verification was skipped).
    """

    verifier_name: str
    passed: bool | None
    confidence: float
    details: dict = field(default_factory=dict)


@dataclass
class TruthReport:
    """Phase 4 evidence object. Explains why final_confidence is what it is.

    TruthReport is never a routing decision — it is evidence that informs one.
    """

    extraction: ExtractionResult
    field_validation: FieldValidationReport
    verification_reports: list[VerificationReport]
    final_confidence: float
    decision_reason: str
