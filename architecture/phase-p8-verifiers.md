# P8 — Verifiers and CI

**Status:** ✅ Done  
**Scope:** Deterministic verifier functions, self-consistency voting, CI pipeline setup

---

## What P8 delivered

P8 adds the Python-side verifier implementations and wires up the GitHub Actions CI
pipeline. Verifiers run deterministic mathematical checks that the LLM cannot be
trusted to perform reliably (check digits, arithmetic, date ordering). The CI pipeline
enforces lint, type checking, migration round-trips, and test gates on every push.

---

## Deterministic verifiers

`agents/verifiers.py` — all verifier functions. Each returns `{"valid": bool, ...}`.

### MRZ check digit (`mrz_checksum`)

ICAO 9303 check-digit algorithm applied to the first 9 characters of `mrz_line2`
(the document number field):

```python
def mrz_checksum(mrz_string: str, check_digit: int) -> dict:
    WEIGHTS = [7, 3, 1]
    charset = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<"
    values = {c: i for i, c in enumerate(charset)}
    total = sum(values[c] * WEIGHTS[i % 3] for i, c in enumerate(mrz_string))
    computed = total % 10
    return {"valid": computed == check_digit, "computed": computed, "expected": check_digit}
```

`<` maps to 0 (MRZ filler character). The check digit is `mrz_line2[9]`.

### Balance arithmetic (`balance_arithmetic`)

```python
def balance_arithmetic(opening: float, closing: float, transactions: list[float]) -> dict:
    computed = opening + sum(transactions)
    tolerance = 0.01
    valid = abs(computed - closing) <= tolerance
    return {"valid": valid, "computed": computed, "expected": closing, "delta": computed - closing}
```

The `_extract_balance_args` extractor in `verifier_registry.py` provides the
`transactions` list using the running `balance` column (last transaction row) when
available, to avoid double-counting "Opening Balance" marker rows.

### Passport date consistency (`passport_date_consistency`)

```python
def passport_date_consistency(issue_date: str, expiry_date: str, birth_date: str | None = None) -> dict:
    # Parses ISO dates, checks: birth_date < issue_date < expiry_date
    ...
```

### Statement period ordering (`statement_period_ordering`)

```python
def statement_period_ordering(start_date: str, end_date: str) -> dict:
    # Parses dates, checks: start < end
    ...
```

### GSTIN checksum (`gstin_checksum`)

Validates the 15-character Indian GST Identification Number format and mod-36
check digit (last character).

### Invoice total consistency (`invoice_total_consistency`)

```python
def invoice_total_consistency(subtotal: float, tax_amount: float, total: float) -> dict:
    computed = subtotal + tax_amount
    valid = abs(computed - total) <= 0.01
    ...
```

### Gross consistency (`gross_consistency`)

```python
def gross_consistency(basic: float, allowances: list[float], gross: float) -> dict:
    computed = basic + sum(allowances)
    valid = abs(computed - gross) <= 0.01
    ...
```

### PAN validation (`pan_validation`)

```python
import re
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")

def pan_validation(pan: str) -> dict:
    valid = bool(_PAN_RE.match(pan.upper()))
    return {"valid": valid, "pan": pan}
```

### AY/FY consistency (`ay_fy_consistency`)

```python
def ay_fy_consistency(assessment_year: str, financial_year: str) -> dict:
    # Assessment Year must be Financial Year + 1
    # e.g. AY="2024-25", FY="2023-24"
    ...
```

### Deed date consistency (`deed_date_consistency`)

```python
def deed_date_consistency(execution_date: str, registration_date: str) -> dict:
    # execution_date ≤ registration_date
    ...
```

---

## Self-consistency voting

`agents/self_consistency.py`:

```python
def should_vote(confidence: float) -> bool:
    return 0.60 <= confidence < 0.85

def vote(results: list[AgentResult]) -> AgentResult:
    # Per-field: count occurrences of each value across results
    # Mode value wins; tie-break: value from highest-confidence sample
    ...
```

Self-consistency is triggered by `extract_agent` when the first extraction returns
confidence in the range `[0.60, 0.85)`. Running 3 independent extractions and taking
the per-field majority vote reduces per-field errors without requiring a full retry.

---

## CI pipeline

`.github/workflows/ci.yml` — four jobs on every push to `main`:

### lint

```yaml
- run: ruff check .
- run: ruff format --check .
- run: mypy .
```

Python version: **3.12** (CI uses 3.12; local dev uses 3.11 — both supported).

### migrations

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16

steps:
  - run: alembic upgrade head
  - run: alembic downgrade base
  - run: alembic upgrade head
```

Round-trip test: upgrade → downgrade to base → upgrade again. Catches migrations that
don't cleanly reverse or that conflict with each other.

### unit-tests

```yaml
- run: pytest tests/unit -m "not live"
```

All external I/O mocked:
- DB: `mock.patch("db.session.get_session")`
- LLM: `mock.patch("agents.llm_client.generate")`
- Object store: `mock.patch("adapters.factory.get_object_store")`

No Docker required for unit tests.

### integration-tests

```yaml
needs: [lint, migrations, unit-tests]
services:
  postgres: pgvector/pgvector:pg16
```

Uses `testcontainers` to spin up a real Postgres. LLM and object store are mocked.
Tests cover the full ingest → classify → extract → truth → resolve → normalize →
persist pipeline with real DB writes and reads.

### e2e-tests

```yaml
if: false   # Gated until GCP deployment
```

End-to-end tests require a live GCP environment (Cloud Run, GCS, Cloud SQL). Deferred
to P14.

---

## pyproject.toml configuration

```toml
[tool.ruff]
line-length = 100
select = ["E", "F", "I", "B"]

[tool.mypy]
ignore_missing_imports = true
# per-module ignores for pgvector, testcontainers, streamlit_agraph, fitz

[tool.pytest.ini_options]
asyncio_mode = "auto"
markers = ["live: marks tests requiring live API calls"]
```

The `live` marker gates any test that makes real Gemini or object store calls.
`make test` runs `pytest -m "not live"`. `make test-live` runs all tests.
