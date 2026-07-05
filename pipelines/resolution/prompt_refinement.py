"""Deterministic prompt refinement strategy.

PromptRefinementStrategy inspects TruthReport evidence and generates
focused additional instructions that are appended to the base extraction
prompt on the next attempt. No LLM is involved in generation — all rules
are deterministic and evidence-driven.

failure_variant() is shared with ResolutionPlanner so both components agree
on what constitutes "the same failure pattern" for deduplication purposes.
"""
from __future__ import annotations

from pipelines.resolution.models import RefinedPrompt
from pipelines.truth_engine.models import TruthReport

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
    """Generates a RefinedPrompt from TruthReport evidence.

    All generation is deterministic. No LLM is called.
    The returned RefinedPrompt.additional_instructions is appended to
    the base extraction prompt on the next op_a_retry pass.
    """

    def __init__(self, coverage_threshold: float = 0.80) -> None:
        self._coverage_threshold = coverage_threshold

    def generate(self, truth_report: TruthReport) -> RefinedPrompt:
        """Inspect evidence and generate focused additional instructions."""
        variant = failure_variant(truth_report, self._coverage_threshold)
        instructions, reason, targets = self._build(truth_report)
        return RefinedPrompt(
            additional_instructions=instructions,
            refinement_reason=reason,
            prompt_variant=variant,
            target_fields=targets,
        )

    # ------------------------------------------------------------------ private

    def _build(self, truth_report: TruthReport) -> tuple[str, str, list[str]]:
        """Return (additional_instructions, refinement_reason, target_fields)."""
        failed_verifiers = [
            r.verifier_name for r in truth_report.verification_reports if r.passed is False
        ]
        if failed_verifiers:
            return self._verifier_instructions(failed_verifiers)

        missing = truth_report.field_validation.required_fields_missing
        if missing:
            return self._missing_field_instructions(missing)

        if truth_report.field_validation.coverage_score < self._coverage_threshold:
            return (
                "Inspect all sections of the document — including headers, footers, "
                "margins, and the back of the document — before completing extraction. "
                "Do not stop at the first readable section.",
                "Insufficient schema coverage detected.",
                [],
            )

        return (
            "Be more careful and precise in extraction. "
            "Read each field value directly from the document text rather than inferring it. "
            "If a value is ambiguous, choose the most explicit occurrence.",
            "Extraction confidence below threshold.",
            [],
        )

    @staticmethod
    def _missing_field_instructions(missing: list[str]) -> tuple[str, str, list[str]]:
        lines = [
            f"The following required fields were not extracted on the previous attempt. "
            f"Look carefully for each one:"
        ]
        for field_name in missing:
            hint = _FIELD_HINTS.get(field_name)
            if hint:
                lines.append(f"- {field_name}: {hint}")
            else:
                lines.append(
                    f"- {field_name}: Search all sections of the document for this field."
                )
        return (
            "\n".join(lines),
            f"Required fields missing after extraction: {missing}.",
            list(missing),
        )

    @staticmethod
    def _verifier_instructions(failed_verifiers: list[str]) -> tuple[str, str, list[str]]:
        lines = [
            f"Deterministic verification failed for: {', '.join(failed_verifiers)}. "
            f"Re-extract the relevant fields with greater care:"
        ]
        for verifier in failed_verifiers:
            hint = _VERIFIER_HINTS.get(verifier)
            if hint:
                lines.append(f"- {verifier}: {hint}")
            else:
                lines.append(
                    f"- {verifier}: Re-extract the fields covered by this verifier "
                    f"from the original document text."
                )
        return (
            "\n".join(lines),
            f"Deterministic verification failed: {failed_verifiers}.",
            [],
        )
