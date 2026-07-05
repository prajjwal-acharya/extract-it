import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from agents.llm_client import generate
from db.models import SchemaVersion

log = logging.getLogger(__name__)

# Nested/array field schema evolution is unsupported — only top-level scalar
# fields participate in discovery, diff, and relaxation. Diffing into array
# items (e.g. transaction line-item sub-schemas) is a separate design decision
# and is explicitly deferred.

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

    Also flags active required scalar fields with no fuzzy match among discovered keys
    as candidates for relaxation (instance-scoped absence, not a value error).

    Array-type fields are excluded from both matching and relaxation — an array field
    being absent from a flat discovery pass does not imply it should be made optional.
    Nested item-level diffing is unsupported and deferred.
    """
    # Only scalar (non-array) fields participate in matching and relaxation.
    scalar_fields = [f for f in active_fields if f.get("type") != "array"]
    normalized_active = {f["name"]: normalize_key(f["name"]) for f in scalar_fields}

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
        for f in scalar_fields
        if f.get("required", True) and f["name"] not in matched_active
    ]

    return SchemaDiff(additions=additions, relaxed_fields=relaxed_fields)


def _bump_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{int(parts[1]) + 1}"
    return f"{version}.next"  # defensive fallback for unexpected version strings


def propose_diff(
    active_row: SchemaVersion,
    diff: SchemaDiff,
    origin_document_id: str,
) -> "SchemaProposal":
    """Create a SchemaProposal from a non-empty diff without writing to the database.

    The proposal is returned to the caller (stored in GraphState) and requires
    explicit human approval before apply_diff() is called to activate it.
    No SchemaVersion is written here.
    """
    from pipelines.learning.schema_proposal import SchemaProposal

    return SchemaProposal(
        doc_type=active_row.doc_type,
        proposed_version=_bump_version(active_row.version),
        additions=diff.additions,
        relaxed_fields=diff.relaxed_fields,
        origin_document_id=origin_document_id,
    )


def apply_diff(
    session: Session,
    active_row: SchemaVersion,
    diff: SchemaDiff,
    origin_document_id: str,
) -> SchemaVersion:
    """Persist a new SchemaVersion (reference ⊕ diff) and atomically flip is_active.

    Called only when a SchemaProposal has been explicitly approved by a human.
    Not invoked automatically by the pipeline — use propose_diff() for that.
    """
    new_fields = [dict(f) for f in active_row.fields_json]
    # Guard: array-type fields have nested structure; only collect names for flat scalar fields
    # to avoid false-positive duplicate suppression or KeyErrors on array field dicts.
    existing_names = {f["name"] for f in new_fields if f.get("type") != "array"}

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
