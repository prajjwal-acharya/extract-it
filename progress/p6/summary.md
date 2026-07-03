# P6 — Normalize + Output Writer

## Deliverables shipped

| File | Change |
|---|---|
| `config/schemas/passport.yaml` | Added `universal_mapping` section |
| `config/schemas/bank_statement.yaml` | Added `universal_mapping` section (`expiry_date: null`) |
| `config/schema_loader.py` | Added `load_universal_mapping(doc_type)` |
| `pipelines/nodes/normalize.py` | Stub → full implementation |
| `io_pipeline/output_writer.py` | Stub → full implementation |
| `tests/unit/test_pipelines.py` | Unstubbed `test_normalize_node_produces_universal_schema` |
| `tests/unit/test_io_pipeline.py` | Unstubbed 3 `write_output` tests |
| `tests/integration/test_p6_p7_normalize.py` | Unstubbed all 3 integration tests |

## Design decisions

**universal_mapping in YAML** — mapping co-located with schema, reuses hot-reload infra, no new config file.

**holder_name as format template** — `"{given_names} {surname}"` resolved via `str.format(**fields)`. Plain field names resolved by direct dict lookup first (bank statement's `account_holder`).

**expiry_date: null for bank_statement** — always 3 universal keys present; `None` when not applicable. Keeps shape consistent for P8 query layer.

**3 ConfidenceLog rows per document** — one per agent (classify/extract/validate). The `agent` column exists specifically to support multi-signal confidence tracking. Collapsible to 1 row later if needed.

**status computation** — `error` present → `"failed"`; `hitl_required and not hitl_approved` → `"rejected"`; else `"completed"`.

## Test results

```
54 passed, 24 failed (all stubs), 4 deselected
```

Baseline was 47 passed. Delta: +7 tests (exactly the 7 unstubbed).
Remaining 24 failures are all `NotImplementedError` stubs for P7–P9.

## Out of scope (deferred)

- `scripts/run_local_demo.py` — needs `build_graph()` (P7)
- `ExtractionResult` table population — not in `write_output` stub docstring
