from __future__ import annotations

import logging

from config.settings import settings
from pipelines.state import GraphState
from pipelines.truth_engine.confidence import ConfidenceFusionPolicy
from pipelines.truth_engine.models import (
    EvidenceBundle,
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
    VerificationReport,
)
from pipelines.truth_engine.verifier_registry import VERIFIER_VERSION, verifier_registry

log = logging.getLogger(__name__)

_policy = ConfidenceFusionPolicy()


def truth_engine_node(state: GraphState) -> dict:
    """Transform ExtractionResult into a TruthReport.

    Sequence: field validation → verifier execution → evidence bundle →
    confidence fusion → persistence policy → TruthReport assembly.
    Produces evidence only — no routing decisions are made here.
    """
    doc_type = state.get("doc_type") or ""
    classify_confidence = state.get("classify_confidence") or 0.0

    # Prefer the typed ExtractionResult stored by extract_node; reconstruct
    # from flat state fields when unavailable (e.g. after op_a_retry).
    extraction: ExtractionResult = state.get("extraction_result") or ExtractionResult(
        fields=state.get("extracted_fields") or {},
        overall_confidence=state.get("extract_confidence") or 0.0,
        context_used=False,
        sample_count=1,
    )

    # 1. Field validation — compare extracted fields against reference schema
    field_validation = FieldValidationReport.build(extraction.fields, doc_type)
    log.info(
        "event=FieldValidation doc_type=%s coverage=%.3f "
        "required_present=%d required_missing=%d additional=%d",
        doc_type,
        field_validation.coverage_score,
        len(field_validation.required_fields_present),
        len(field_validation.required_fields_missing),
        len(field_validation.additional_fields),
    )

    # 2. Verifier execution — run every registered verifier, collect all reports
    verification_reports: list[VerificationReport] = []
    for spec in verifier_registry.get(doc_type):
        kwargs = spec.extractor(extraction.fields)
        if kwargs is None:
            report = VerificationReport(
                verifier_name=spec.name,
                passed=None,
                confidence=0.0,
                details={"reason": "required_fields_not_present"},
            )
        else:
            try:
                result = spec.fn(**kwargs)
                passed = bool(result.get("valid", False))
                report = VerificationReport(
                    verifier_name=spec.name,
                    passed=passed,
                    confidence=1.0 if passed else 0.0,
                    details=result,
                )
            except Exception as exc:
                report = VerificationReport(
                    verifier_name=spec.name,
                    passed=None,
                    confidence=0.0,
                    details={"error": str(exc)},
                )
        log.info(
            "event=VerifierExecuted verifier=%s doc_type=%s passed=%s confidence=%.2f",
            report.verifier_name,
            doc_type,
            report.passed,
            report.confidence,
        )
        verification_reports.append(report)

    # 3. Confidence fusion via EvidenceBundle
    bundle = EvidenceBundle(
        classify_confidence=classify_confidence,
        extraction_confidence=extraction.overall_confidence,
        coverage_score=field_validation.coverage_score,
        verification_reports=verification_reports,
    )
    final_confidence, decision_reason = _policy.fuse(bundle)
    log.info(
        "event=ConfidenceFusion doc_type=%s final=%.4f reason=%s",
        doc_type,
        final_confidence,
        decision_reason,
    )

    # 4. Persistence decision — sole authority on document_status and P6 flags
    persistence = PersistenceDecision.from_truth(
        verification_reports=verification_reports,
        final_confidence=final_confidence,
        threshold=settings.CONFIDENCE_THRESHOLD,
    )

    # 5. Assemble TruthReport — evidence + decision, verifier_version for audit replay
    truth_report = TruthReport(
        extraction=extraction,
        field_validation=field_validation,
        verification_reports=verification_reports,
        final_confidence=final_confidence,
        decision_reason=decision_reason,
        persistence=persistence,
        verifier_version=VERIFIER_VERSION,
    )
    log.info(
        "event=TruthReportCreated doc_type=%s final_confidence=%.4f "
        "verifiers_run=%d verifiers_passed=%d document_status=%s verifier_version=%s",
        doc_type,
        final_confidence,
        len(verification_reports),
        sum(1 for r in verification_reports if r.passed is True),
        persistence.document_status,
        VERIFIER_VERSION,
    )

    return {"truth_report": truth_report}
