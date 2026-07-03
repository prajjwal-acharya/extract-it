import re

from dateutil import parser as date_parser

from config.schema_loader import load_universal_mapping
from pipelines.state import GraphState

_UNIVERSAL_KEYS = ("holder_name", "id_number", "expiry_date")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _resolve(template: str | None, fields: dict) -> str | None:
    if template is None:
        return None
    if template in fields:
        return fields[template]
    try:
        return template.format(**fields)
    except (KeyError, IndexError):
        return None


def _canonicalize_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        # ISO dates are unambiguous — dayfirst=True would reinterpret YYYY-MM-DD
        # as YYYY-DD-MM (e.g. "2030-02-10" → Oct 2 instead of Feb 10).
        day_first = not bool(_ISO_DATE.match(value))
        return date_parser.parse(value, dayfirst=day_first).date().isoformat()
    except (ValueError, TypeError):
        return value  # leave as-is if unparseable, don't silently drop


def normalize_node(state: GraphState) -> dict:
    """Map doc-type-specific extracted fields to the universal schema."""
    doc_type = state.get("doc_type")
    fields = state.get("extracted_fields") or {}
    try:
        mapping = load_universal_mapping(doc_type) if doc_type else {}
    except FileNotFoundError:
        mapping = {}

    universal = {key: _resolve(mapping.get(key), fields) for key in _UNIVERSAL_KEYS}
    universal["expiry_date"] = _canonicalize_date(universal["expiry_date"])
    return {"universal_schema": universal}
