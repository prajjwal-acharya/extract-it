from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, create_model

_SCHEMA_DIR = Path(__file__).parent / "schemas"
_CACHE: dict[str, tuple[float, type[BaseModel]]] = {}

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


def load_universal_mapping(doc_type: str) -> dict:
    """Return the universal_mapping section of doc_type's YAML schema."""
    path = _SCHEMA_DIR / f"{doc_type}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No schema for doc_type={doc_type!r}")
    raw = yaml.safe_load(path.read_text())
    return raw.get("universal_mapping", {})


def load_schema_model(doc_type: str) -> type[BaseModel]:
    """Build (or return cached) Pydantic model from config/schemas/<doc_type>.yaml.

    Hot-reloads when the YAML file's mtime changes.
    """
    path = _SCHEMA_DIR / f"{doc_type}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"No schema for doc_type={doc_type!r}")

    mtime = path.stat().st_mtime
    cached = _CACHE.get(doc_type)
    if cached and cached[0] == mtime:
        return cached[1]

    raw = yaml.safe_load(path.read_text())
    base = _build_model(f"{doc_type}_Schema", raw["fields"])
    model = create_model(f"{doc_type}_ExtractResponse", __base__=base, confidence=(float, ...))
    _CACHE[doc_type] = (mtime, model)
    return model
