# P10 — Normalization

**Status:** ✅ Done  
**Scope:** normalize_node, universal_schema, date canonicalization, fallback mapping

---

## What P10 delivered

P10 introduces the normalization step that runs after every successful extraction
(and after every HITL decision, regardless of outcome). It maps doc-type-specific
extracted fields to three canonical universal fields, enabling cross-document
comparison, search, and analytics without knowing the specific doc type.

---

## normalize_node position in the graph

```
strategy_executor → normalize  (ACCEPT path)
op_b_hitl → normalize          (always — both approve and reject)
normalize → persist
```

`route_after_hitl` always returns `"normalize"`. This invariant ensures that
`universal_schema` is computed for every document, including rejected ones.

---

## Universal schema

Three canonical fields, regardless of `doc_type`:

| Key | Description |
|---|---|
| `holder_name` | Primary identity holder (person name or organisation name) |
| `id_number` | Primary document identifier (passport number, account number, Aadhaar number, etc.) |
| `expiry_date` | Expiry/validity end date (ISO 8601 `YYYY-MM-DD`, or `null`) |

Stored in `Document.universal_schema` as:
```json
{
  "holder_name": "JOHN SMITH",
  "id_number": "A12345678",
  "expiry_date": "2029-06-15"
}
```

---

## Universal mapping per doc type

Each YAML schema defines `universal_mapping`:

```yaml
# passport.yaml
universal_mapping:
  holder_name: "{given_names} {surname}"   # format string
  id_number:   passport_number              # plain field name
  expiry_date: date_of_expiry

# bank_statement.yaml
universal_mapping:
  holder_name: account_holder
  id_number:   account_number              # may be null for UK banks (use fallback)
  expiry_date: null

# aadhaar.yaml
universal_mapping:
  holder_name: full_name
  id_number:   aadhaar_number
  expiry_date: null
```

`null` in `expiry_date` means this doc type has no expiry concept.

---

## _resolve function

`pipelines/nodes/normalize.py` — `_resolve(mapping_value, fields)`:

```python
def _resolve(mapping_value: str | None, fields: dict) -> str | None:
    if mapping_value is None:
        return None
    if "{" in mapping_value:
        # Format string: "{given_names} {surname}" → "JOHN SMITH"
        try:
            return mapping_value.format(**fields)
        except KeyError:
            return None
    # Plain field name: "passport_number" → fields.get("passport_number")
    return fields.get(mapping_value)
```

---

## Fallback mapping

Some doc types have a `universal_mapping_fallback` in their YAML for secondary
resolution when the primary mapping returns `None`:

```yaml
# bank_statement.yaml
universal_mapping:
  id_number: account_number         # UK banks may not have this
universal_mapping_fallback:
  id_number: iban                   # use IBAN instead
```

`config/schema_loader.py` — `load_universal_mapping_fallback(doc_type)` reads
`universal_mapping_fallback` from YAML directly (always YAML, not DB — this is a
structural mapping that changes with the YAML schema, not with runtime discovery).

```python
for key in _UNIVERSAL_KEYS:   # ["holder_name", "id_number", "expiry_date"]
    value = _resolve(mapping.get(key), fields)
    if value is None and key in fallback_mapping:
        value = _resolve(fallback_mapping[key], fields)
    universal[key] = value
```

---

## Date canonicalization

`_canonicalize_date(value: str | None) -> str | None`:

Normalises various date string formats to ISO 8601 `YYYY-MM-DD`:

| Input format | Output |
|---|---|
| `"15 JAN 2024"` | `"2024-01-15"` |
| `"01/15/2024"` | `"2024-01-15"` |
| `"2024-01-15"` | `"2024-01-15"` (passthrough) |
| `"15-01-2024"` | `"2024-01-15"` |
| `None` | `None` |

Applied only to `expiry_date` in the universal schema. Doc-type-specific date fields
in `extracted_fields` are stored as-is.

---

## normalize_node return

```python
def normalize_node(state: GraphState) -> dict:
    doc_type = state.get("doc_type")
    fields = state.get("extracted_fields") or {}

    try:
        mapping = load_universal_mapping(doc_type) if doc_type else {}
    except FileNotFoundError:
        mapping = {}

    try:
        fallback_mapping = load_universal_mapping_fallback(doc_type) if doc_type else {}
    except FileNotFoundError:
        fallback_mapping = {}

    universal = {}
    for key in _UNIVERSAL_KEYS:
        value = _resolve(mapping.get(key), fields)
        if value is None and key in fallback_mapping:
            value = _resolve(fallback_mapping[key], fields)
        universal[key] = value

    universal["expiry_date"] = _canonicalize_date(universal["expiry_date"])
    return {"universal_schema": universal}
```

Returns only `{"universal_schema": {...}}` — does not modify any other GraphState field.

---

## Why normalize runs even on rejection

A rejected document has been seen and reviewed — its `universal_schema` should be
populated for analytics and completeness. The `Document.status` is set to `"rejected"`
by `_compute_terminal_status`, not by `normalize_node`. The normalize step is purely
a data transformation with no side effects on document status.

This also simplifies the graph: `op_b_hitl → normalize → persist` is the same code
path regardless of the human decision, with only `persist` reading `hitl_approved`
to compute the terminal status.
