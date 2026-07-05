import json
import logging

from sqlalchemy import select

from adapters.factory import get_object_store
from agents.extract_agent import extract
from agents.llm_client import embed
from agents.schema_diff_agent import diff_schema, discover_fields, propose_diff
from db.models import RetrievalLog, SchemaVersion
from db.session import session_scope
from db.vector_store import similarity_search
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename

log = logging.getLogger(__name__)


def _build_schema_proposal(
    session, doc_type: str, raw_bytes: bytes, mime_type: str, document_id: str
) -> tuple[str | None, dict | None]:
    """Discover fields against the active schema and create a proposal if a diff exists.

    Phase 5.5: schema changes are never auto-applied.  A SchemaProposal is returned
    and stored in state; a human must approve it before apply_diff() is called.
    Returns (current_schema_version, proposal_dict | None).
    """
    try:
        active_row = session.execute(
            select(SchemaVersion).where(
                SchemaVersion.doc_type == doc_type, SchemaVersion.is_active.is_(True)
            )
        ).scalar_one_or_none()
        if active_row is None:
            return None, None

        discovered = discover_fields(raw_bytes, mime_type)
        diff = diff_schema(discovered, active_row.fields_json)

        if diff.is_empty:
            return active_row.version, None

        proposal = propose_diff(active_row, diff, origin_document_id=document_id)
        log.info(
            "schema_proposal: doc_type=%s proposed %s -> %s (+%d fields, %d relaxed) [awaiting approval]",
            doc_type,
            active_row.version,
            proposal.proposed_version,
            len(proposal.additions),
            len(proposal.relaxed_fields),
        )
        return active_row.version, proposal.to_dict()
    except Exception as e:
        log.warning("schema_proposal failed for doc_type=%s: %s", doc_type, e)
        return None, None


def op_a_retry_node(state: GraphState) -> dict:
    """Re-run extraction with strategy-selected enhancements.

    Consumes and then clears the following strategy-specific state fields so
    they do not persist across passes when the next strategy is different:

      refined_prompt           — PROMPT_REFINEMENT: additional instructions appended
      better_retrieval_queries — BETTER_RETRIEVAL:  targeted queries override default RAG
      preprocessed_bytes       — IMAGE_PREPROCESS:  rasterised/enhanced bytes
      preprocessed_mime_type   — IMAGE_PREPROCESS:  mime type after processing
      model_override           — MODEL_ESCALATION:  escalation model for this pass only
    """
    doc_type = state.get("doc_type") or ""
    document_id = state["document_id"]
    mime_type = mime_from_filename(state["filename"])

    # -- Strategy: IMAGE_PREPROCESS --
    preprocessed_bytes = state.get("preprocessed_bytes")
    preprocessed_mime = state.get("preprocessed_mime_type")
    if preprocessed_bytes:
        raw_bytes = preprocessed_bytes
        mime_type = preprocessed_mime or mime_type
        log.info("event=RetryWithPreprocessedBytes mime_type=%s", mime_type)
    else:
        raw_bytes = state.get("raw_bytes") or get_object_store().get(state["object_key"])

    # -- Strategy: BETTER_RETRIEVAL or standard RAG --
    better_queries = state.get("better_retrieval_queries")

    with session_scope() as session:
        schema_version, schema_proposal = _build_schema_proposal(
            session, doc_type, raw_bytes, mime_type, document_id
        )

        if better_queries:
            # BETTER_RETRIEVAL: run multiple targeted queries, deduplicate by document_id
            log.info("event=BetterRetrieval query_count=%d", len(better_queries))
            seen_ids: set[str] = set()
            all_similar: list = []
            for query_str in better_queries:
                results = similarity_search(
                    session,
                    embed(query_str, task_type="RETRIEVAL_QUERY"),
                    top_k=2,
                    doc_type=doc_type,
                )
                for row, dist in results:
                    if row.document_id not in seen_ids:
                        seen_ids.add(row.document_id)
                        all_similar.append((row, dist))
            similar = sorted(all_similar, key=lambda x: x[1])[:5]
        else:
            # Standard RAG: embed extracted_fields JSON
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

    # -- Strategy: PROMPT_REFINEMENT --
    refined_prompt = state.get("refined_prompt")
    additional_instructions = refined_prompt.additional_instructions if refined_prompt else None

    # -- Strategy: MODEL_ESCALATION --
    model_override = state.get("model_override")
    if model_override:
        log.info("event=EscalatedModelRetry model=%s", model_override)

    result = extract(
        raw_bytes,
        mime_type,
        doc_type,
        context=context,
        additional_instructions=additional_instructions,
        model_override=model_override,
    )

    return {
        "extracted_fields": result.fields,
        "extract_confidence": result.overall_confidence,
        "extraction_result": result,
        "retry_count": state["retry_count"] + 1,
        "schema_version": schema_version,
        "schema_proposal": schema_proposal,  # None when schema is current; dict when changes proposed
        # Clear all strategy fields after consumption
        "refined_prompt": None,
        "better_retrieval_queries": None,
        "preprocessed_bytes": None,
        "preprocessed_mime_type": None,
        "model_override": None,
    }
