from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, create_model
from sqlalchemy import select

from db.models import SchemaVersion
from db.session import get_session

_SCHEMA_DIR = Path(__file__).parent / "schemas"
# Cache key includes version so a schema_versions flip invalidates automatically —
# no mtime tracking needed for the DB path (YAML fallback still uses mtime).
_CACHE: dict[str, tuple[str, type[BaseModel]]] = {}

_TYPE_MAP: dict[str, Any] = {
    "string": str,
    "date": str,  # format/regex validation deferred to P4 (validate)
    "float": float,
    "integer": int,
}


def _field_type(field: dict) -> Any:
    if "enum" in field:
        # Literal[tuple] passes the same tuple to __getitem__ as Literal[a, b, c]
        return Literal[tuple(field["enum"])]  # type: ignore[misc]
    if field["type"] == "array":
        item_model = _build_model(f"{field['name']}_Item", field["items"])
        return list[item_model]  # type: ignore[valid-type]
    return _TYPE_MAP[field["type"]]


def _build_model(name: str, fields: list[dict]) -> type[BaseModel]:
    kwargs: dict[str, Any] = {}
    for f in fields:
        py_type = _field_type(f)
        required = f.get("required", True)
        if required:
            kwargs[f["name"]] = (py_type, ...)
        else:
            kwargs[f["name"]] = (Optional[py_type], None)  # type: ignore[assignment]
    return create_model(name, **kwargs)


def _load_yaml_raw(doc_type: str) -> dict:
    path = _SCHEMA_DIR / f"{doc_type}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No schema for doc_type={doc_type!r}")
    return yaml.safe_load(path.read_text())


def _load_active_row(doc_type: str) -> SchemaVersion | None:
    """Return the active schema_versions row for doc_type, or None if DB unavailable/unseeded.

    Postgres is the source of truth once seeded (via migration or auto-discovery);
    YAML is only the bootstrap fallback — never mutated at runtime.
    """
    try:
        session = get_session()
        try:
            return session.execute(
                select(SchemaVersion).where(
                    SchemaVersion.doc_type == doc_type, SchemaVersion.is_active.is_(True)
                )
            ).scalar_one_or_none()
        finally:
            session.close()
    except Exception:
        return None


def load_reference_fields(doc_type: str) -> tuple[list[str], list[str]]:
    """Return (required_fields, optional_fields) for doc_type (DB-first, YAML fallback).

    Returns ([], []) when no reference schema exists — callers should still produce
    an open extraction prompt, just without specific field guidance.
    """
    row = _load_active_row(doc_type)
    if row is not None:
        fields = row.fields_json
    else:
        try:
            fields = _load_yaml_raw(doc_type).get("fields", [])
        except FileNotFoundError:
            return [], []
    required = [f["name"] for f in fields if f.get("required", True)]
    optional = [f["name"] for f in fields if not f.get("required", True)]
    return required, optional


def load_universal_mapping(doc_type: str) -> dict:
    """Return the active universal_mapping for doc_type (DB-first, YAML fallback)."""
    row = _load_active_row(doc_type)
    if row is not None:
        return row.universal_mapping_json
    return _load_yaml_raw(doc_type).get("universal_mapping", {})


def load_universal_mapping_fallback(doc_type: str) -> dict:
    """Return the universal_mapping_fallback from YAML (always YAML — no DB override).

    Keys match _UNIVERSAL_KEYS; values are field names tried when the primary
    mapping resolves to None (e.g. 'iban' as fallback for 'id_number').
    """
    return _load_yaml_raw(doc_type).get("universal_mapping_fallback", {})


def load_schema_model(doc_type: str) -> type[BaseModel]:
    """Build (or return cached) Pydantic model for doc_type's active schema.

    Cache key is the resolved version string, so an auto-discovery version bump
    invalidates the cache automatically on next lookup.
    """
    row = _load_active_row(doc_type)
    if row is not None:
        version_key = f"db:{row.doc_type}:{row.version}"
        cached = _CACHE.get(doc_type)
        if cached and cached[0] == version_key:
            return cached[1]
        base = _build_model(f"{doc_type}_Schema", row.fields_json)
    else:
        raw = _load_yaml_raw(doc_type)
        mtime = (_SCHEMA_DIR / f"{doc_type}.yaml").stat().st_mtime
        version_key = f"yaml:{doc_type}:{mtime}"
        cached = _CACHE.get(doc_type)
        if cached and cached[0] == version_key:
            return cached[1]
        base = _build_model(f"{doc_type}_Schema", raw["fields"])

    model = create_model(f"{doc_type}_ExtractResponse", __base__=base, confidence=(float, ...))
    _CACHE[doc_type] = (version_key, model)
    return model
