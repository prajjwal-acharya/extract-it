from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

MAX_TOOL_CALLS = 3


@dataclass
class VerifierSpec:
    """One deterministic verifier that can be run against extracted fields.

    extractor(fields) maps the raw extracted-fields dict to the kwargs for fn,
    or returns None when required fields are absent (verifier not attempted).
    """

    name: str
    fn: Callable[..., dict]  # fn(**kwargs) -> {"valid": bool, ...}
    description: str = ""
    extractor: Callable[[dict], dict | None] = field(
        default_factory=lambda: (lambda fields: None)
    )


# ---------------------------------------------------------------------------
# Field extractors for registered verifiers
# ---------------------------------------------------------------------------


def _extract_mrz_args(fields: dict) -> dict | None:
    """Extract MRZ document-number + check-digit from mrz_line2.

    Passport MRZ line 2 layout (ICAO 9303):
        chars 0-8  → document number (9 chars, may include '<' filler)
        char  9    → check digit for document number
    Returns None if mrz_line2 is absent or too short to parse.
    """
    mrz_line2 = fields.get("mrz_line2")
    if not isinstance(mrz_line2, str) or len(mrz_line2) < 10:
        return None
    mrz_string = mrz_line2[:9]
    try:
        check_digit = int(mrz_line2[9])
    except (ValueError, IndexError):
        return None
    return {"mrz_string": mrz_string, "check_digit": check_digit}


def _extract_balance_args(fields: dict) -> dict | None:
    """Extract opening/closing balances and transaction amounts.

    Tries common field-name variants produced by open extraction.
    Transactions may be plain floats or dicts with an 'amount' key.
    Returns None when opening or closing is absent.
    """
    opening = (
        fields.get("opening_balance")
        if fields.get("opening_balance") is not None
        else fields.get("opening")
    )
    closing = (
        fields.get("closing_balance")
        if fields.get("closing_balance") is not None
        else fields.get("closing")
    )
    if opening is None or closing is None:
        return None
    raw_transactions = fields.get("transactions") or []
    try:
        amounts: list[float] = []
        for t in raw_transactions:
            if isinstance(t, dict):
                amounts.append(float(t.get("amount", 0.0)))
            else:
                amounts.append(float(t))
        return {
            "opening": float(opening),
            "closing": float(closing),
            "transactions": amounts,
        }
    except (TypeError, ValueError):
        return None


class VerifierRegistry:
    """Maps document types to their deterministic verifiers.

    Adding a verifier for a new doc type requires only a single register() call —
    no changes to graph nodes or routing.
    """

    def __init__(self) -> None:
        self._registry: dict[str, list[VerifierSpec]] = {}

    def register(self, doc_type: str, *specs: VerifierSpec) -> "VerifierRegistry":
        self._registry.setdefault(doc_type, []).extend(specs)
        return self

    def get(self, doc_type: str) -> list[VerifierSpec]:
        return list(self._registry.get(doc_type, []))

    def has_verifiers(self, doc_type: str) -> bool:
        return bool(self._registry.get(doc_type))

    def all_doc_types(self) -> list[str]:
        return list(self._registry.keys())


from agents.verifiers import balance_arithmetic, mrz_checksum

verifier_registry = VerifierRegistry()

verifier_registry.register(
    "passport",
    VerifierSpec(
        name="mrz_checksum",
        fn=mrz_checksum,
        description="Verify MRZ field check digits per ICAO 9303",
        extractor=_extract_mrz_args,
    ),
)
verifier_registry.register(
    "bank_statement",
    VerifierSpec(
        name="balance_arithmetic",
        fn=balance_arithmetic,
        description="Verify opening + sum(transactions) ≈ closing balance (±0.01)",
        extractor=_extract_balance_args,
    ),
)
# Placeholders — verifiers will be registered here as they are implemented:
# verifier_registry.register("gst_invoice",   VerifierSpec("gstin_checksum", ...))
# verifier_registry.register("salary_slip",   VerifierSpec(...))
# verifier_registry.register("property_deed", VerifierSpec(...))
