from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

MAX_TOOL_CALLS = 3
VERIFIER_VERSION = "1.0"


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
# Passport extractors
# ---------------------------------------------------------------------------


def _extract_mrz_args(fields: dict) -> dict | None:
    """Chars 0-8 of mrz_line2 → document number; char 9 → check digit."""
    mrz_line2 = fields.get("mrz_line2")
    if not isinstance(mrz_line2, str) or len(mrz_line2) < 10:
        return None
    try:
        return {"mrz_string": mrz_line2[:9], "check_digit": int(mrz_line2[9])}
    except (ValueError, IndexError):
        return None


def _extract_passport_dates(fields: dict) -> dict | None:
    issue = fields.get("date_of_issue")
    expiry = fields.get("date_of_expiry")
    if not issue or not expiry:
        return None
    kwargs: dict = {"issue_date": issue, "expiry_date": expiry}
    dob = fields.get("date_of_birth")
    if dob:
        kwargs["birth_date"] = dob
    return kwargs


# ---------------------------------------------------------------------------
# Bank Statement extractors
# ---------------------------------------------------------------------------


def _extract_balance_args(fields: dict) -> dict | None:
    """Resolve opening/closing aliases; handle dict or float transactions."""
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
    raw = fields.get("transactions") or []
    try:
        amounts: list[float] = [
            float(t.get("amount", 0.0)) if isinstance(t, dict) else float(t) for t in raw
        ]
        return {"opening": float(opening), "closing": float(closing), "transactions": amounts}
    except (TypeError, ValueError):
        return None


def _extract_period_args(fields: dict) -> dict | None:
    start = fields.get("statement_start_date") or fields.get("period_start")
    end = fields.get("statement_end_date") or fields.get("period_end")
    if not start or not end:
        return None
    return {"start_date": start, "end_date": end}


# ---------------------------------------------------------------------------
# GST Invoice extractors
# ---------------------------------------------------------------------------


def _extract_gstin_args(fields: dict) -> dict | None:
    gstin = fields.get("gstin") or fields.get("seller_gstin")
    if not gstin:
        return None
    return {"gstin": gstin}


def _extract_invoice_total_args(fields: dict) -> dict | None:
    subtotal = fields.get("subtotal") or fields.get("taxable_value")
    tax = fields.get("tax_amount") or fields.get("total_tax")
    total = fields.get("invoice_total") or fields.get("total_amount") or fields.get("grand_total")
    if subtotal is None or tax is None or total is None:
        return None
    try:
        return {"subtotal": float(subtotal), "tax_amount": float(tax), "total": float(total)}
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Salary Slip extractors
# ---------------------------------------------------------------------------


def _extract_gross_args(fields: dict) -> dict | None:
    basic = fields.get("basic_salary") or fields.get("basic")
    gross = fields.get("gross_salary") or fields.get("gross")
    if basic is None or gross is None:
        return None
    raw = fields.get("allowances") or []
    try:
        amounts: list[float] = [
            float(a.get("amount", 0.0)) if isinstance(a, dict) else float(a) for a in raw
        ]
        return {"basic": float(basic), "allowances": amounts, "gross": float(gross)}
    except (TypeError, ValueError):
        return None


def _extract_pan_from_salary(fields: dict) -> dict | None:
    pan = fields.get("pan") or fields.get("employee_pan")
    if not pan:
        return None
    return {"pan": pan}


# ---------------------------------------------------------------------------
# ITR extractors
# ---------------------------------------------------------------------------


def _extract_pan_from_itr(fields: dict) -> dict | None:
    pan = fields.get("pan") or fields.get("pan_number")
    if not pan:
        return None
    return {"pan": pan}


def _extract_ay_fy_args(fields: dict) -> dict | None:
    ay = fields.get("assessment_year") or fields.get("ay")
    fy = fields.get("financial_year") or fields.get("fy")
    if not ay or not fy:
        return None
    return {"assessment_year": ay, "financial_year": fy}


# ---------------------------------------------------------------------------
# Property Deed extractors
# ---------------------------------------------------------------------------


def _extract_deed_dates(fields: dict) -> dict | None:
    execution = fields.get("execution_date") or fields.get("deed_date")
    registration = fields.get("registration_date")
    if not execution or not registration:
        return None
    return {"execution_date": execution, "registration_date": registration}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


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


from agents.verifiers import (
    ay_fy_consistency,
    balance_arithmetic,
    deed_date_consistency,
    gross_consistency,
    gstin_checksum,
    invoice_total_consistency,
    mrz_checksum,
    pan_validation,
    passport_date_consistency,
    statement_period_ordering,
)

verifier_registry = VerifierRegistry()

verifier_registry.register(
    "passport",
    VerifierSpec(
        name="mrz_checksum",
        fn=mrz_checksum,
        description="Verify MRZ document-number check digit per ICAO 9303",
        extractor=_extract_mrz_args,
    ),
    VerifierSpec(
        name="passport_date_consistency",
        fn=passport_date_consistency,
        description="Verify birth_date < issue_date < expiry_date",
        extractor=_extract_passport_dates,
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
    VerifierSpec(
        name="statement_period_ordering",
        fn=statement_period_ordering,
        description="Verify statement start_date < end_date",
        extractor=_extract_period_args,
    ),
)

verifier_registry.register(
    "gst_invoice",
    VerifierSpec(
        name="gstin_checksum",
        fn=gstin_checksum,
        description="Validate GSTIN format and mod-36 check digit",
        extractor=_extract_gstin_args,
    ),
    VerifierSpec(
        name="invoice_total_consistency",
        fn=invoice_total_consistency,
        description="Verify subtotal + tax_amount ≈ invoice total (±0.01)",
        extractor=_extract_invoice_total_args,
    ),
)

verifier_registry.register(
    "salary_slip",
    VerifierSpec(
        name="gross_consistency",
        fn=gross_consistency,
        description="Verify basic + sum(allowances) ≈ gross salary (±0.01)",
        extractor=_extract_gross_args,
    ),
    VerifierSpec(
        name="pan_validation",
        fn=pan_validation,
        description="Validate employee PAN format",
        extractor=_extract_pan_from_salary,
    ),
)

verifier_registry.register(
    "itr",
    VerifierSpec(
        name="pan_validation",
        fn=pan_validation,
        description="Validate PAN number format",
        extractor=_extract_pan_from_itr,
    ),
    VerifierSpec(
        name="ay_fy_consistency",
        fn=ay_fy_consistency,
        description="Verify Assessment Year = Financial Year + 1",
        extractor=_extract_ay_fy_args,
    ),
)

verifier_registry.register(
    "property_deed",
    VerifierSpec(
        name="deed_date_consistency",
        fn=deed_date_consistency,
        description="Verify execution_date ≤ registration_date",
        extractor=_extract_deed_dates,
    ),
)
