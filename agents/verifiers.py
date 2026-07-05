"""Deterministic document-field verifiers.

Every function returns {"valid": bool, ...}. Failures are non-fatal — the
Truth Engine collects all reports and factors them into final_confidence.
"""
from __future__ import annotations

import re
from datetime import date

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_MRZ_WEIGHTS = [7, 3, 1]
_MRZ_CHAR_VALUES: dict[str, int] = {str(i): i for i in range(10)}
_MRZ_CHAR_VALUES.update({chr(65 + i): 10 + i for i in range(26)})
_MRZ_CHAR_VALUES["<"] = 0

_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_AY_FY_RE = re.compile(r"^(20\d\d)-(\d\d)$")


def _parse_date(value: str | None) -> date | None:
    """Parse a date string to a date object using dateutil for robustness."""
    if not isinstance(value, str) or not value.strip():
        return None
    from dateutil import parser as dp

    try:
        # ISO dates (YYYY-MM-DD) must not be treated as dayfirst to avoid
        # reinterpreting 2030-02-10 as Oct 2 instead of Feb 10.
        day_first = not bool(re.match(r"^\d{4}-", value))
        return dp.parse(value, dayfirst=day_first).date()
    except (ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# MRZ / Passport
# ---------------------------------------------------------------------------


def mrz_checksum(mrz_string: str, check_digit: int) -> dict:
    """Verify an MRZ field check digit using the ICAO 9303 weighted-sum algorithm.

    Returns {"valid": bool, "expected": int, "got": int}.
    """
    total = sum(
        _MRZ_WEIGHTS[i % 3] * _MRZ_CHAR_VALUES.get(c, 0) for i, c in enumerate(mrz_string)
    )
    expected = total % 10
    return {"valid": expected == check_digit, "expected": expected, "got": check_digit}


def passport_date_consistency(
    issue_date: str,
    expiry_date: str,
    birth_date: str | None = None,
) -> dict:
    """Verify date ordering: birth_date < issue_date < expiry_date.

    Returns {"valid": bool, "checks": {check_name: bool}}.
    Partial failure if some dates are unparseable — only available pairs checked.
    """
    d_issue = _parse_date(issue_date)
    d_expiry = _parse_date(expiry_date)
    d_birth = _parse_date(birth_date) if birth_date else None

    checks: dict[str, bool] = {}
    if d_issue is not None and d_expiry is not None:
        checks["issue_before_expiry"] = d_issue < d_expiry
    if d_birth is not None and d_issue is not None:
        checks["birth_before_issue"] = d_birth < d_issue

    valid = bool(checks) and all(checks.values())
    return {"valid": valid, "checks": checks}


# ---------------------------------------------------------------------------
# Bank Statement
# ---------------------------------------------------------------------------


def balance_arithmetic(opening: float, closing: float, transactions: list[float]) -> dict:
    """Verify that opening + sum(transactions) reconciles to closing balance (±0.01).

    Returns {"valid": bool, "computed_closing": float, "expected_closing": float}.
    """
    computed = round(opening + sum(transactions), 2)
    expected = round(closing, 2)
    return {
        "valid": abs(computed - expected) < 0.01,
        "computed_closing": computed,
        "expected_closing": expected,
    }


def statement_period_ordering(start_date: str, end_date: str) -> dict:
    """Verify that statement start_date is strictly before end_date.

    Returns {"valid": bool, "start": str, "end": str}.
    """
    d_start = _parse_date(start_date)
    d_end = _parse_date(end_date)
    if d_start is None or d_end is None:
        return {"valid": False, "reason": "unparseable_date", "start": start_date, "end": end_date}
    return {"valid": d_start < d_end, "start": str(d_start), "end": str(d_end)}


# ---------------------------------------------------------------------------
# GST Invoice
# ---------------------------------------------------------------------------


def gstin_checksum(gstin: str) -> dict:
    """Validate GSTIN format and mod-36 check digit.

    Returns {"valid": bool, "expected": str | None, "got": str}.
    """
    gstin = (gstin or "").strip().upper()
    if len(gstin) != 15:
        return {"valid": False, "reason": "invalid_length", "got": gstin}
    if not _GSTIN_RE.match(gstin):
        return {"valid": False, "reason": "format_mismatch", "got": gstin}

    total = 0
    for i, c in enumerate(gstin[:14]):
        idx = _GSTIN_CHARS.find(c)
        if idx < 0:
            return {"valid": False, "reason": f"invalid_char:{c}", "got": gstin}
        val = idx * (1 if i % 2 == 0 else 2)
        total += (val // 36) + (val % 36)

    expected_idx = (36 - (total % 36)) % 36
    expected = _GSTIN_CHARS[expected_idx]
    actual = gstin[14]
    return {"valid": actual == expected, "expected": expected, "got": actual}


def invoice_total_consistency(subtotal: float, tax_amount: float, total: float) -> dict:
    """Verify subtotal + tax_amount ≈ total (±0.01).

    Returns {"valid": bool, "computed_total": float, "expected_total": float}.
    """
    computed = round(subtotal + tax_amount, 2)
    expected = round(total, 2)
    return {
        "valid": abs(computed - expected) < 0.01,
        "computed_total": computed,
        "expected_total": expected,
    }


# ---------------------------------------------------------------------------
# Salary Slip
# ---------------------------------------------------------------------------


def gross_consistency(basic: float, allowances: list[float], gross: float) -> dict:
    """Verify basic + sum(allowances) ≈ gross (±0.01).

    Returns {"valid": bool, "computed_gross": float, "expected_gross": float}.
    """
    computed = round(basic + sum(allowances), 2)
    expected = round(gross, 2)
    return {
        "valid": abs(computed - expected) < 0.01,
        "computed_gross": computed,
        "expected_gross": expected,
    }


def pan_validation(pan: str) -> dict:
    """Validate Indian PAN number format ([A-Z]{5}[0-9]{4}[A-Z]).

    Returns {"valid": bool, "pan": str}.
    """
    pan_clean = (pan or "").strip().upper()
    valid = bool(_PAN_RE.match(pan_clean))
    return {"valid": valid, "pan": pan_clean}


# ---------------------------------------------------------------------------
# ITR
# ---------------------------------------------------------------------------


def ay_fy_consistency(assessment_year: str, financial_year: str) -> dict:
    """Verify AY is exactly one year after FY.

    Expected format: "YYYY-YY", e.g. FY "2022-23", AY "2023-24".
    Returns {"valid": bool, "assessment_year": str, "financial_year": str}.
    """
    m_ay = _AY_FY_RE.match((assessment_year or "").strip())
    m_fy = _AY_FY_RE.match((financial_year or "").strip())
    if not m_ay or not m_fy:
        return {
            "valid": False,
            "reason": "format_mismatch",
            "assessment_year": assessment_year,
            "financial_year": financial_year,
        }
    ay_start = int(m_ay.group(1))
    fy_start = int(m_fy.group(1))
    valid = ay_start == fy_start + 1
    return {
        "valid": valid,
        "assessment_year": assessment_year,
        "financial_year": financial_year,
    }


# ---------------------------------------------------------------------------
# Property Deed
# ---------------------------------------------------------------------------


def deed_date_consistency(execution_date: str, registration_date: str) -> dict:
    """Verify execution_date ≤ registration_date for a property deed.

    Returns {"valid": bool, "execution": str, "registration": str}.
    """
    d_exec = _parse_date(execution_date)
    d_reg = _parse_date(registration_date)
    if d_exec is None or d_reg is None:
        return {
            "valid": False,
            "reason": "unparseable_date",
            "execution": execution_date,
            "registration": registration_date,
        }
    return {
        "valid": d_exec <= d_reg,
        "execution": str(d_exec),
        "registration": str(d_reg),
    }
