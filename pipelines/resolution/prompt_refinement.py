"""Deterministic prompt refinement strategy.

PromptRefinementStrategy inspects TruthReport evidence and generates
focused additional instructions that are appended to the base extraction
prompt on the next attempt. No LLM is involved in generation — all rules
are deterministic and evidence-driven.

Phase 5.4: PromptRefinementStrategy now delegates instruction generation to
DirectiveEngine so that the evidence→instruction mapping is defined in one
place and shared with BETTER_RETRIEVAL and IMAGE_PREPROCESS.

failure_variant() is shared with ResolutionPlanner so both components agree
on what constitutes "the same failure pattern" for deduplication purposes.
"""

from __future__ import annotations

from pipelines.resolution.directives import Directive, DirectiveEngine, _FIELD_DIRECTIVES
from pipelines.resolution.models import RefinedPrompt
from pipelines.truth_engine.models import TruthReport

_directive_engine = DirectiveEngine()

# ---------------------------------------------------------------------------
# Field-specific extraction hints
# ---------------------------------------------------------------------------

_FIELD_HINTS: dict[str, str] = {
    "passport_number": (
        "Focus on the MRZ (Machine Readable Zone) at the bottom of the document. "
        "The passport number is 9 characters: 1 letter followed by 7 alphanumeric "
        "characters and 1 check digit."
    ),
    "pan_number": (
        "Search for a PAN (Permanent Account Number) — a 10-character alphanumeric "
        "code in the format AAAAA0000A (5 letters, 4 digits, 1 letter)."
    ),
    "pan": (
        "Search for a PAN (Permanent Account Number) — a 10-character alphanumeric "
        "code in the format AAAAA0000A (5 letters, 4 digits, 1 letter)."
    ),
    "date_of_birth": (
        "Look for date of birth — it may appear in DD/MM/YYYY or YYMMDD format. "
        "In MRZ documents it occupies positions 14-19 of line 2 (YYMMDD)."
    ),
    "date_of_expiry": (
        "The expiry date is typically on MRZ line 2, positions 21-26 (YYMMDD), "
        "and may also appear in the visual inspection zone."
    ),
    "date_of_issue": (
        "The issue date usually appears in the visual zone between the photo and "
        "the MRZ. Look for labels like 'Date of Issue', 'Issue Date', or 'Issued'."
    ),
    "mrz_line1": (
        "Extract MRZ Line 1 verbatim — it starts with 'P<' for passports and "
        "contains type, issuing state, and the name field."
    ),
    "mrz_line2": (
        "Extract MRZ Line 2 verbatim — it starts with the document number (9 chars) "
        "followed by a check digit, nationality, birth date, sex, expiry date."
    ),
    "surname": (
        "The surname appears after 'P<<' in MRZ Line 1 and is also printed in the "
        "visual inspection zone. Extract only the family name."
    ),
    "given_names": (
        "Given names appear in MRZ Line 1 after the surname, separated by '<'. "
        "They are also printed in the visual inspection zone."
    ),
    "nationality": (
        "Nationality is a 3-letter ISO 3166-1 alpha-3 code in the MRZ "
        "(e.g., IND=India, GBR=United Kingdom, USA=United States)."
    ),
    "sex": (
        "Sex/gender appears at position 21 of MRZ line 2 as a single character: "
        "M (male), F (female), or < (unspecified)."
    ),
    "place_of_birth": (
        "Place of birth is usually in the lower visual inspection zone, "
        "labeled 'Place of Birth', 'Lieu de naissance', or similar."
    ),
    "issuing_authority": (
        "The issuing authority is typically labeled 'Authority', 'Issued by', "
        "or 'Issuing Authority' in the visual zone or on the back of the document."
    ),
}

# ---------------------------------------------------------------------------
# Verifier-to-field hint mapping
# ---------------------------------------------------------------------------

_VERIFIER_HINTS: dict[str, str] = {
    "mrz_check": (
        "Re-extract MRZ lines 1 and 2 verbatim including '<' separators. "
        "The MRZ check digits are computed from adjacent fields — accuracy is critical."
    ),
    "date_check": (
        "Re-extract all date fields (date_of_birth, date_of_expiry, date_of_issue) "
        "in their original format from the document. Do not reformat or infer dates."
    ),
    "field_presence": (
        "Re-check all business-critical fields and extract any that are missing. "
        "Look at all sections including headers, footers, and the back of the document."
    ),
    "confidence_check": (
        "Increase extraction precision — verify each extracted value against the "
        "visible text before including it."
    ),
}


# ---------------------------------------------------------------------------
# Shared variant computation — imported by both planner and PromptRefinementStrategy
# ---------------------------------------------------------------------------


def failure_variant(truth_report: TruthReport, coverage_threshold: float = 0.80) -> str:
    """Compute a canonical slug identifying the failure pattern.

    Used by ResolutionPlanner to detect duplicate refinement attempts and by
    PromptRefinementStrategy to label the generated RefinedPrompt. Both must
    use the same function so their understanding of "same failure" is identical.

    Priority mirrors planner rule ordering so the most actionable cause is named.
    """
    failed_verifiers = sorted(
        r.verifier_name for r in truth_report.verification_reports if r.passed is False
    )
    if failed_verifiers:
        return f"verifier_failure:{','.join(failed_verifiers)}"

    missing = sorted(truth_report.field_validation.required_fields_missing[:5])
    if missing:
        return f"missing_fields:{','.join(missing)}"

    if truth_report.field_validation.coverage_score < coverage_threshold:
        return "low_coverage"

    return "low_confidence"


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class PromptRefinementStrategy:
    """Generates a RefinedPrompt from TruthReport evidence via DirectiveEngine.

    Phase 5.4: instruction text is now generated from typed Directive labels
    via DirectiveEngine so that the evidence→instruction mapping is shared
    with BETTER_RETRIEVAL and IMAGE_PREPROCESS. The external interface is
    unchanged: generate(truth_report) → RefinedPrompt.
    """

    def __init__(self, coverage_threshold: float = 0.80) -> None:
        self._coverage_threshold = coverage_threshold

    def generate(self, truth_report: TruthReport) -> RefinedPrompt:
        """Inspect evidence and generate focused additional instructions.

        Instructions are built from:
          1. Directive-based generic guidance (shared mapping from DirectiveEngine)
          2. Specific context appended for verifier names and uncovered field names
             so the LLM receives actionable, document-specific hints.
        """
        variant = failure_variant(truth_report, self._coverage_threshold)
        directives = _directive_engine.generate(truth_report)
        parts = [_directive_engine.to_prompt_instructions(directives)]

        # Append specific verifier names so the model knows which checks failed
        failed_verifiers = [
            r.verifier_name for r in truth_report.verification_reports if r.passed is False
        ]
        if failed_verifiers:
            parts.append(f"Specifically re-check fields for: {', '.join(failed_verifiers)}.")

        # Append field names for required fields NOT covered by a known directive
        missing = truth_report.field_validation.required_fields_missing
        uncovered = [f for f in missing if f not in _FIELD_DIRECTIVES]
        if uncovered:
            parts.append(f"Also search all document sections for: {', '.join(uncovered)}.")

        instructions = "\n".join(parts)
        reason = self._reason_from_directives(directives, truth_report)
        targets = self._target_fields_from_truth(truth_report)
        return RefinedPrompt(
            additional_instructions=instructions,
            refinement_reason=reason,
            prompt_variant=variant,
            target_fields=targets,
        )

    # ------------------------------------------------------------------ private

    @staticmethod
    def _reason_from_directives(directives: list[Directive], truth_report: TruthReport) -> str:
        """Build a concise human-readable reason from the directives selected."""
        if any(d == Directive.RECHECK_EXTRACTION for d in directives):
            failed = [
                r.verifier_name for r in truth_report.verification_reports if r.passed is False
            ]
            return f"Deterministic verification failed: {failed}."
        missing = truth_report.field_validation.required_fields_missing
        if missing:
            return f"Required fields missing after extraction: {missing}."
        cov = truth_report.field_validation.coverage_score
        if cov < 0.80:
            return f"Insufficient schema coverage: {cov:.2%}."
        return "Extraction confidence below threshold."

    @staticmethod
    def _target_fields_from_truth(truth_report: TruthReport) -> list[str]:
        return list(truth_report.field_validation.required_fields_missing)
