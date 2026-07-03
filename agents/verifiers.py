"""Deterministic document-field verifiers exposed as Gemini function-calling tools."""

_MRZ_WEIGHTS = [7, 3, 1]

_CHAR_VALUES: dict[str, int] = {str(i): i for i in range(10)}
_CHAR_VALUES.update({chr(65 + i): 10 + i for i in range(26)})
_CHAR_VALUES["<"] = 0


def mrz_checksum(mrz_string: str, check_digit: int) -> dict:
    """Verify an MRZ field check digit using the ICAO 9303 weighted-sum algorithm.

    Returns {"valid": bool, "expected": int, "got": int}.
    """
    total = sum(_MRZ_WEIGHTS[i % 3] * _CHAR_VALUES.get(c, 0) for i, c in enumerate(mrz_string))
    expected = total % 10
    return {"valid": expected == check_digit, "expected": expected, "got": check_digit}


def balance_arithmetic(opening: float, closing: float, transactions: list[float]) -> dict:
    """Verify that opening + sum(transactions) reconciles to closing balance (±0.01).

    Returns {"valid": bool, "computed_closing": float, "expected_closing": float}.
    """
    computed = round(opening + sum(transactions), 2)
    expected = round(closing, 2)
    return {"valid": abs(computed - expected) < 0.01, "computed_closing": computed, "expected_closing": expected}
