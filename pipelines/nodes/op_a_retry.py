import json
import logging

from sqlalchemy import select

from adapters.factory import get_object_store
from agents.extract_agent import extract
from agents.llm_client import embed
from agents.schema_diff_agent import apply_diff, diff_schema, discover_fields
from agents.validate_agent import validate
from db.models import RetrievalLog, SchemaVersion
from db.session import get_session
from db.vector_store import similarity_search
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename

log = logging.getLogger(__name__)


def _run_schema_discovery(
    session, doc_type: str, raw_bytes: bytes, mime_type: str, document_id: str
) -> str | None:
    """Discover+diff+apply against the active schema. Best-effort — never blocks extraction."""
    try:
        active_row = session.execute(
            select(SchemaVersion).where(
                SchemaVersion.doc_type == doc_type, SchemaVersion.is_active.is_(True)
            )
        ).scalar_one_or_none()
        if active_row is None:
            return None  # no seeded reference row — fall through to YAML path unchanged

        discovered = discover_fields(raw_bytes, mime_type)
        diff = diff_schema(discovered, active_row.fields_json)
        if diff.is_empty:
            return active_row.version

        new_version = apply_diff(session, active_row, diff, origin_document_id=document_id)
        log.info(
            "schema_diff_agent: doc_type=%s promoted %s -> %s (+%d fields, %d relaxed)",
            doc_type,
            active_row.version,
            new_version.version,
            len(diff.additions),
            len(diff.relaxed_fields),
        )
        return new_version.version
    except Exception as e:
        # Discovery is an enhancement, not a correctness dependency — extraction
        # must proceed against the current active schema regardless of failure here.
        log.warning("schema_diff_agent failed for doc_type=%s: %s", doc_type, e)
        return None


def op_a_retry_node(state: GraphState) -> dict:
    """Re-run extraction augmented with RAG context, after an auto-schema-evolution pass."""
    doc_type = state.get("doc_type") or ""
    session = get_session()

    document_id = state["document_id"]
    raw_bytes = state.get("raw_bytes") or get_object_store().get(state["object_key"])
    mime_type = mime_from_filename(state["filename"])

    schema_version = _run_schema_discovery(session, doc_type, raw_bytes, mime_type, document_id)

    query_text = json.dumps(state.get("extracted_fields") or {})
    similar = similarity_search(
        session, embed(query_text, task_type="RETRIEVAL_QUERY"), top_k=3, doc_type=doc_type
    )
    context = "\n".join(f"Example: {row.chunk_text}" for row, _ in similar) or None

    for row, distance in similar:
        if row.document_id == document_id:
            continue
        session.add(
            RetrievalLog(
                document_id=document_id,
                retrieved_document_id=row.document_id,
                stage="retry",
                similarity_score=1 - distance,
            )
        )
    session.commit()

    # load_schema_model/load_universal_mapping re-query schema_versions internally,
    # so extract() below transparently picks up any version bump made above.
    result = extract(raw_bytes, mime_type, doc_type, context=context)
    validate_result = validate(doc_type, result.data)

    return {
        "extracted_fields": result.data,
        "extract_confidence": result.confidence,
        "validation_issues": validate_result.data.get("issues", []),
        "validate_confidence": validate_result.confidence,
        "retry_count": state["retry_count"] + 1,
        "schema_version": schema_version,
    }
