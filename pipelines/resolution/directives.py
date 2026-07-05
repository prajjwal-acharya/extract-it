"""Refinement Directive Engine — structured evidence-to-directive mapping.

Directives are typed, canonical labels that describe what recovery action is
needed based on TruthReport evidence. They replace free-text reasoning and are
shared across all autonomous strategies:

  PROMPT_REFINEMENT → converts directives to focused prompt instructions
  BETTER_RETRIEVAL  → converts directives to targeted RAG query strings
  IMAGE_PREPROCESS  → converts directives to preprocessing operation names
  MODEL_ESCALATION  → uses ESCALATE_PRECISION to gate escalation
"""
from __future__ import annotations

from enum import Enum

from pipelines.truth_engine.models import TruthReport

# ---------------------------------------------------------------------------
# Directive vocabulary
# ---------------------------------------------------------------------------


class Directive(str, Enum):
    """Typed label for a targeted recovery action.

    Each directive has a clear, single-purpose meaning so that strategy
    implementations can map it to concrete actions without ambiguity.
    """

    FOCUS_MRZ = "FOCUS_MRZ"
    SEARCH_PAN = "SEARCH_PAN"
    VERIFY_TOTALS = "VERIFY_TOTALS"
    CHECK_SIGNATURE = "CHECK_SIGNATURE"
    SEARCH_PROPERTY_METADATA = "SEARCH_PROPERTY_METADATA"
    EXPAND_RETRIEVAL = "EXPAND_RETRIEVAL"
    HIGH_CONTRAST_READ = "HIGH_CONTRAST_READ"
    INSPECT_ALL_SECTIONS = "INSPECT_ALL_SECTIONS"
    ESCALATE_PRECISION = "ESCALATE_PRECISION"
    RECHECK_EXTRACTION = "RECHECK_EXTRACTION"


# ---------------------------------------------------------------------------
# Field → directive mapping
# ---------------------------------------------------------------------------

_FIELD_DIRECTIVES: dict[str, Directive] = {
    "passport_number": Directive.FOCUS_MRZ,
    "mrz_line1": Directive.FOCUS_MRZ,
    "mrz_line2": Directive.FOCUS_MRZ,
    "surname": Directive.FOCUS_MRZ,
    "given_names": Directive.FOCUS_MRZ,
    "nationality": Directive.FOCUS_MRZ,
    "date_of_birth": Directive.FOCUS_MRZ,
    "date_of_expiry": Directive.FOCUS_MRZ,
    "pan_number": Directive.SEARCH_PAN,
    "pan": Directive.SEARCH_PAN,
    "total_amount": Directive.VERIFY_TOTALS,
    "net_amount": Directive.VERIFY_TOTALS,
    "gross_amount": Directive.VERIFY_TOTALS,
    "signature": Directive.CHECK_SIGNATURE,
    "property_address": Directive.SEARCH_PROPERTY_METADATA,
    "plot_number": Directive.SEARCH_PROPERTY_METADATA,
    "registration_number": Directive.SEARCH_PROPERTY_METADATA,
}

# ---------------------------------------------------------------------------
# Directive → prompt instruction text
# ---------------------------------------------------------------------------

_DIRECTIVE_INSTRUCTIONS: dict[Directive, str] = {
    Directive.FOCUS_MRZ: (
        "Focus on the MRZ (Machine Readable Zone) at the bottom of the document. "
        "Extract each MRZ field verbatim, including check digits."
    ),
    Directive.SEARCH_PAN: (
        "Search for a PAN (Permanent Account Number) — a 10-character alphanumeric code "
        "formatted AAAAA0000A (5 letters, 4 digits, 1 letter)."
    ),
    Directive.VERIFY_TOTALS: (
        "Re-extract all financial totals. Cross-check line items against the final totals "
        "to ensure consistency."
    ),
    Directive.CHECK_SIGNATURE: (
        "Locate and extract signature information from the document. "
        "Note its position and any associated authority label."
    ),
    Directive.SEARCH_PROPERTY_METADATA: (
        "Extract all property identifiers including address, plot number, survey number, "
        "and registration details."
    ),
    Directive.EXPAND_RETRIEVAL: (
        "Retrieve additional context from related document examples before extraction."
    ),
    Directive.HIGH_CONTRAST_READ: (
        "Read the document carefully — contrast may be low. "
        "Extract from the clearest available representation of each field."
    ),
    Directive.INSPECT_ALL_SECTIONS: (
        "Inspect all sections of the document including headers, footers, margins, "
        "and the reverse side before completing extraction."
    ),
    Directive.ESCALATE_PRECISION: (
        "Apply maximum precision. Verify each extracted value against the visible document "
        "text before including it."
    ),
    Directive.RECHECK_EXTRACTION: (
        "Re-extract fields that previously failed verification. "
        "Read each value directly from the document text without inference."
    ),
}

# ---------------------------------------------------------------------------
# Directive → RAG retrieval query fragment
# ---------------------------------------------------------------------------

_DIRECTIVE_QUERIES: dict[Directive, str] = {
    Directive.FOCUS_MRZ: "passport MRZ machine readable zone identity document number",
    Directive.SEARCH_PAN: "PAN permanent account number tax identification AAAAA0000A",
    Directive.VERIFY_TOTALS: "financial document totals invoiced amount net gross",
    Directive.CHECK_SIGNATURE: "signature authority approval document signing",
    Directive.SEARCH_PROPERTY_METADATA: "property registration plot survey address legal",
    Directive.EXPAND_RETRIEVAL: "document extraction comprehensive fields",
    Directive.HIGH_CONTRAST_READ: "document low contrast extraction difficult scan",
    Directive.INSPECT_ALL_SECTIONS: "complete document full extraction all sections fields",
    Directive.ESCALATE_PRECISION: "high precision extraction verification accuracy",
    Directive.RECHECK_EXTRACTION: "re-extraction verification failed fields careful",
}

# ---------------------------------------------------------------------------
# Directive → preprocessing operations
# ---------------------------------------------------------------------------

_DIRECTIVE_PREPROCESSING: dict[Directive, list[str]] = {
    Directive.HIGH_CONTRAST_READ: ["contrast_enhance", "sharpen"],
    Directive.FOCUS_MRZ: ["sharpen", "denoise"],
    Directive.INSPECT_ALL_SECTIONS: ["render_hires"],
    Directive.SEARCH_PAN: ["contrast_enhance"],
    Directive.RECHECK_EXTRACTION: ["contrast_enhance", "sharpen"],
}

_CONFIDENCE_ESCALATION_THRESHOLD = 0.50  # below this → ESCALATE_PRECISION always triggered


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class DirectiveEngine:
    """Maps TruthReport evidence to typed Directive lists.

    All strategy implementations (PROMPT_REFINEMENT, BETTER_RETRIEVAL,
    IMAGE_PREPROCESS, MODEL_ESCALATION) read directives from here so that
    the evidence → action mapping is defined in one place.
    """

    def generate(self, truth_report: TruthReport) -> list[Directive]:
        """Return ordered, deduplicated directives for the current failure state."""
        seen: set[Directive] = set()
        directives: list[Directive] = []

        def _add(d: Directive) -> None:
            if d not in seen:
                seen.add(d)
                directives.append(d)

        # Verifier failures → RECHECK_EXTRACTION
        for report in truth_report.verification_reports:
            if report.passed is False:
                _add(Directive.RECHECK_EXTRACTION)

        # Missing required fields → field-specific directives
        for field_name in truth_report.field_validation.required_fields_missing:
            directive = _FIELD_DIRECTIVES.get(field_name)
            if directive is not None:
                _add(directive)

        # Low schema coverage → broader search
        if truth_report.field_validation.coverage_score < 0.80:
            _add(Directive.INSPECT_ALL_SECTIONS)
            _add(Directive.EXPAND_RETRIEVAL)

        # Very low confidence → high contrast + escalation
        if truth_report.final_confidence < _CONFIDENCE_ESCALATION_THRESHOLD:
            _add(Directive.HIGH_CONTRAST_READ)
            _add(Directive.ESCALATE_PRECISION)

        # Catch-all when nothing specific was triggered
        if not directives:
            _add(Directive.ESCALATE_PRECISION)

        return directives

    def to_prompt_instructions(self, directives: list[Directive]) -> str:
        """Convert directives to a focused additional-instructions block for PromptBuilder."""
        lines = [_DIRECTIVE_INSTRUCTIONS[d] for d in directives]
        return "\n".join(lines)

    def to_retrieval_queries(
        self, directives: list[Directive], doc_type: str
    ) -> list[str]:
        """Convert directives to targeted RAG query strings."""
        queries: list[str] = []
        seen: set[str] = set()
        for d in directives:
            fragment = _DIRECTIVE_QUERIES.get(d)
            if fragment and fragment not in seen:
                seen.add(fragment)
                # Include doc_type to bias similarity search toward relevant documents
                queries.append(f"{doc_type} {fragment}")
        if not queries:
            queries.append(doc_type)
        return queries

    def to_preprocessing_ops(self, directives: list[Directive]) -> list[str]:
        """Convert directives to a deduplicated, ordered list of preprocessing operation names."""
        seen: set[str] = set()
        ops: list[str] = []
        for d in directives:
            for op in _DIRECTIVE_PREPROCESSING.get(d, []):
                if op not in seen:
                    seen.add(op)
                    ops.append(op)
        return ops
