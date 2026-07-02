from pipelines.state import DocumentState


_UNIVERSAL_KEYS = {
    "passport": {
        "holder_name": lambda f: f"{f.get('given_names', '')} {f.get('surname', '')}".strip(),
        "id_number": lambda f: f.get("passport_number"),
        "expiry_date": lambda f: f.get("date_of_expiry"),
        "issuing_country": lambda f: f.get("nationality"),
    },
    "bank_statement": {
        "holder_name": lambda f: f.get("account_holder"),
        "id_number": lambda f: f.get("account_number"),
        "expiry_date": lambda f: f.get("statement_period_end"),
        "issuing_country": lambda f: f.get("bank_name"),
    },
}


def normalize_node(state: DocumentState) -> dict:
    mapping = _UNIVERSAL_KEYS.get(state.doc_type or "", {})
    universal = {k: fn(state.extracted_fields) for k, fn in mapping.items()}
    universal["doc_type"] = state.doc_type
    universal["raw_fields"] = state.extracted_fields
    return {"universal_schema": universal, "status": "complete"}
