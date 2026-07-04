import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from agents.llm_client import generate
from db.models import SchemaVersion

log = logging.getLogger(__name__)

# Below this similarity, a discovered field is treated as genuinely new rather
# than a naming variant of an existing field (e.g. "acct_holder" vs "account_holder").
_MATCH_THRESHOLD = 0.82

_SNAKE_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class SchemaDiff:
    additions: list[dict] = field(default_factory=list)  # new field dicts, schema-yaml shape
    relaxed_fields: list[str] = field(default_factory=list)  # existing required fields not found

    @property
    def is_empty(self) -> bool:
        return not self.additions and not self.relaxed_fields


def normalize_key(raw_key: str) -> str:
    """Snake-case a discovered field label for stable comparison."""
    return _SNAKE_RE.sub("_", raw_key.strip().lower()).strip("_")


def discover_fields(content: bytes, mime_type: str) -> dict[str, str]:
    """Loose, unschematized extraction — ask Gemini to list every visible field/value pair.

    No response_schema: structured output enforces types at generation time,
    which is exactly what we need to bypass to find fields outside the reference schema.
    """
    prompt = (
        "List every distinct field label and its value visible in this document, "
        "as a flat JSON object mapping field_label -> value. "
        "Include every label even if you're unsure of its meaning. "
        "Respond with JSON only, no other text."
    )
    raw = generate(prompt, image_bytes=content, mime_type=mime_type)
    try:
        cleaned = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        parsed = json.loads(cleaned)
        return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, AttributeError) as e:
        log.warning("discover_fields: failed to parse Gemini output: %s", e)
        return {}


def diff_schema(discovered: dict[str, str], active_fields: list[dict]) -> SchemaDiff:
    """Fuzzy-match discovered keys against active_fields; classify as addition or match.

    Also flags active required fields with no fuzzy match among discovered keys
    as candidates for relaxation (instance-scoped absence, not a value error).
    """
    active_names = [f["name"] for f in active_fields]
    normalized_active = {name: normalize_key(name) for name in active_names}

    matched_active: set[str] = set()
    additions: list[dict] = []

    for raw_key, value in discovered.items():
        norm_key = normalize_key(raw_key)
        best_name, best_ratio = None, 0.0
        for active_name, active_norm in normalized_active.items():
            ratio = SequenceMatcher(None, norm_key, active_norm).ratio()
            if ratio > best_ratio:
                best_name, best_ratio = active_name, ratio

        if best_ratio >= _MATCH_THRESHOLD and best_name is not None:
            matched_active.add(best_name)
            continue

        additions.append({"name": norm_key, "type": "string", "required": False})

    relaxed_fields = [
        f["name"]
        for f in active_fields
        if f.get("required", True) and f["name"] not in matched_active
    ]

    return SchemaDiff(additions=additions, relaxed_fields=relaxed_fields)


def _bump_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{int(parts[1]) + 1}"
    return f"{version}.next"  # defensive fallback for unexpected version strings


def apply_diff(
    session: Session,
    active_row: SchemaVersion,
    diff: SchemaDiff,
    origin_document_id: str,
) -> SchemaVersion:
    """Persist a new SchemaVersion (reference ⊕ diff) and atomically flip is_active.

    Auto-applied per prior decision — no HITL gate on schema evolution itself.
    """
    new_fields = [dict(f) for f in active_row.fields_json]
    existing_names = {f["name"] for f in new_fields}

    for addition in diff.additions:
        if addition["name"] not in existing_names:
            new_fields.append(addition)
            existing_names.add(addition["name"])

    for f in new_fields:
        if f["name"] in diff.relaxed_fields:
            f["required"] = False

    new_version = SchemaVersion(
        doc_type=active_row.doc_type,
        version=_bump_version(active_row.version),
        fields_json=new_fields,
        universal_mapping_json=active_row.universal_mapping_json,
        source="auto_discovered",
        origin_document_id=origin_document_id,
        is_active=True,
    )

    # Deactivate + flush before inserting the new active row — the partial unique
    # index (doc_type WHERE is_active) would otherwise momentarily see two active
    # rows for the same doc_type if insert/update flush ordering isn't guaranteed.
    active_row.is_active = False
    session.add(active_row)
    session.flush()

    session.add(new_version)
    session.commit()
    session.refresh(new_version)
    return new_version
