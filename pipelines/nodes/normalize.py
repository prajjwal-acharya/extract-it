from config.schema_loader import load_universal_mapping
from pipelines.state import GraphState

_UNIVERSAL_KEYS = ("holder_name", "id_number", "expiry_date")


def _resolve(template: str | None, fields: dict) -> str | None:
    if template is None:
        return None
    if template in fields:
        return fields[template]
    try:
        return template.format(**fields)
    except (KeyError, IndexError):
        return None


def normalize_node(state: GraphState) -> dict:
    """Map doc-type-specific extracted fields to the universal schema."""
    doc_type = state.get("doc_type")
    fields = state.get("extracted_fields") or {}
    try:
        mapping = load_universal_mapping(doc_type) if doc_type else {}
    except FileNotFoundError:
        mapping = {}

    universal = {key: _resolve(mapping.get(key), fields) for key in _UNIVERSAL_KEYS}
    return {"universal_schema": universal}
