# P4 — Truth Engine

**Status:** ✅ Done  
**Scope:** Deterministic verifiers, TruthReport, VerifierRegistry, confidence scoring, PersistenceDecision

---

## What P4 delivered

P4 is the post-extraction evidence layer. After extraction, the Truth Engine runs
deterministic verifiers against the extracted fields, scores field coverage, computes
a composite confidence score, and produces a `TruthReport` that the Resolution Engine
uses to decide the next action. The Truth Engine replaces the earlier `validate_agent`
as the sole authority over extraction quality.

---

## Truth Engine node flow

```
truth_engine_node(state)
  1. Load extracted_fields and doc_type from GraphState

  2. Field validation (coverage scoring):
        active_schema = load_reference_fields(doc_type)
        required_present = fields that are in schema.required AND present in extracted
        required_missing = fields that are in schema.required AND absent
        coverage_score = len(required_present) / len(schema.required)
        additional_fields = extracted keys not in schema

  3. Run verifiers:
        specs = verifier_registry.get(doc_type)
        for spec in specs:
            kwargs = spec.extractor(extracted_fields)
            if kwargs is None:
                result = VerificationReport(passed=None, reason="required inputs absent")
            else:
                result = spec.fn(**kwargs)
                result = VerificationReport(passed=result["valid"], ...)
            reports.append(result)

  4. Composite confidence scoring:
        confidence.compute(coverage_score, extraction_confidence, verification_reports)

  5. PersistenceDecision:
        allow_completion = coverage_score ≥ threshold AND no hard verifier failures
        allow_embedding  = allow_completion
        allow_learning   = allow_completion AND confidence ≥ learning_threshold
        document_status  = "completed" | "verification_failed"

  6. Build TruthReport and write to GraphState:
        truth_report = TruthReport(
            field_validation=FieldValidationReport(...),
            verification_reports=[...],
            final_confidence=...,
            decision_reason=...,
            persistence=PersistenceDecision(...),
            verifier_version=VERIFIER_VERSION,
        )
```

---

## VerifierRegistry

`pipelines/truth_engine/verifier_registry.py` maps document types to their deterministic
verifiers. Adding a verifier requires only one `register()` call — no graph node changes.

```python
@dataclass
class VerifierSpec:
    name: str
    fn: Callable[..., dict]           # fn(**kwargs) → {"valid": bool, ...}
    description: str
    extractor: Callable[[dict], dict | None]  # maps extracted_fields → fn kwargs
                                              # returns None to skip verifier
```

### Registered verifiers

| doc_type | Verifier | What it checks |
|---|---|---|
| `passport` | `mrz_checksum` | ICAO 9303 check-digit algorithm on `mrz_line2[0:9]` |
| `passport` | `passport_date_consistency` | `birth_date < issue_date < expiry_date` |
| `bank_statement` | `balance_arithmetic` | `opening + Σ(transactions) ≈ closing ± 0.01` |
| `bank_statement` | `statement_period_ordering` | `start_date < end_date` |
| `gst_invoice` | `gstin_checksum` | GSTIN format + mod-36 check digit |
| `gst_invoice` | `invoice_total_consistency` | `subtotal + tax ≈ total ± 0.01` |
| `salary_slip` | `gross_consistency` | `basic + Σ(allowances) ≈ gross ± 0.01` |
| `salary_slip` | `pan_validation` | PAN format `[A-Z]{5}[0-9]{4}[A-Z]` |
| `itr` | `pan_validation` | PAN format |
| `itr` | `ay_fy_consistency` | Assessment Year = Financial Year + 1 |
| `property_deed` | `deed_date_consistency` | `execution_date ≤ registration_date` |

Documents with no registered verifiers (driving_license, aadhaar) skip the
verification step — `verification_reports` is empty, `allow_completion` is
determined by field coverage alone.

---

## Extractor functions

Each `VerifierSpec.extractor` maps the raw `extracted_fields` dict to the specific
kwargs the verifier function expects. Returns `None` when required inputs are absent,
which means the verifier is **skipped** (not failed).

### Balance arithmetic extractor (bank_statement)

```python
def _extract_balance_args(fields: dict) -> dict | None:
    opening = fields.get("opening_balance") or fields.get("opening")
    closing = fields.get("closing_balance") or fields.get("closing")
    if opening is None or closing is None:
        return None

    # Preferred: use running balance on last transaction row.
    # This avoids double-counting "Opening Balance" marker rows that LLMs
    # often include as the first transaction entry.
    last_balance = None
    for t in reversed(fields.get("transactions") or []):
        if isinstance(t, dict) and t.get("balance") is not None:
            last_balance = float(t["balance"])
            break

    if last_balance is not None:
        return {"opening": float(opening), "closing": float(closing),
                "transactions": [last_balance - float(opening)]}

    # Fallback: sum debit/credit amounts (skip rows with only a running balance).
    amounts = []
    for t in raw:
        if isinstance(t, dict):
            if "amount" in t:
                amounts.append(float(t["amount"]))
            elif t.get("credit") is not None or t.get("debit") is not None:
                credit = float(t.get("credit") or 0.0)
                debit  = float(t.get("debit")  or 0.0)
                amounts.append(credit - debit)
    return {"opening": float(opening), "closing": float(closing), "transactions": amounts}
```

### Statement period extractor (bank_statement)

```python
def _extract_period_args(fields: dict) -> dict | None:
    start = (
        fields.get("statement_period_start")   # first-priority (extractor uses this key)
        or fields.get("statement_start_date")
        or fields.get("period_start")
    )
    end = (
        fields.get("statement_period_end")
        or fields.get("statement_end_date")
        or fields.get("period_end")
    )
    if not start or not end:
        return None
    return {"start_date": start, "end_date": end}
```

---

## TruthReport models

`pipelines/truth_engine/models.py`:

```python
@dataclass
class FieldValidationReport:
    coverage_score: float                 # required fields present / total required
    required_fields_missing: list[str]    # names of absent required fields
    additional_fields: list[str]          # extracted keys not in schema

@dataclass
class VerificationReport:
    verifier_name: str
    passed: bool | None                   # None = skipped (inputs absent)
    confidence: float
    details: str

@dataclass
class PersistenceDecision:
    allow_completion: bool
    allow_embedding: bool
    allow_learning: bool
    document_status: str                  # "completed" | "verification_failed"
    reason: str

@dataclass
class TruthReport:
    field_validation: FieldValidationReport
    verification_reports: list[VerificationReport]
    final_confidence: float
    decision_reason: str
    persistence: PersistenceDecision
    verifier_version: str                 # "1.0" — enables audit replay
```

---

## Confidence scoring

`pipelines/truth_engine/confidence.py` computes the composite confidence:

- Base: extraction confidence from `extract_agent`
- Coverage penalty: reduce by `(1 - coverage_score) × coverage_weight`
- Verifier penalty: reduce for each failed verifier (failed = passed=False, not skipped)
- Final: clamped to [0.0, 1.0]

The composite score drives the `ResolutionPlanner` threshold comparisons.

---

## TruthAuditLog

`write_output` persists the `TruthReport` to `truth_audit_logs`:

```python
TruthAuditLog(
    document_id=...,
    doc_type=...,
    final_confidence=truth_report.final_confidence,
    decision_reason=truth_report.decision_reason,
    coverage_score=truth_report.field_validation.coverage_score,
    required_fields_missing=[...],
    additional_fields=[...],
    verification_reports=[{"verifier_name": ..., "passed": ..., ...}],
    document_status=truth_report.persistence.document_status,
    allow_completion=...,
    allow_embedding=...,
    allow_learning=...,
    persistence_reason=...,
    verifier_version=...,
)
```

One row per pipeline run. Queryable via `GET /documents/{id}` → `truth_audit`.

---

## VERIFIER_VERSION

`verifier_registry.py` exports `VERIFIER_VERSION = "1.0"`. Stored in every
`TruthAuditLog` row. When verifier logic changes (e.g., the balance arithmetic
fix from the double-counting bug), bumping this version lets you identify which
historical audit logs were produced by the old logic and re-evaluate them.
