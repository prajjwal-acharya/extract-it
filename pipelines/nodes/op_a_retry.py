import json

from adapters.factory import get_object_store
from agents.extract_agent import extract
from agents.llm_client import embed
from agents.validate_agent import validate
from db.session import get_session
from db.vector_store import similarity_search
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename


def op_a_retry_node(state: GraphState) -> dict:
    """Re-run extraction augmented with RAG context from pgvector similarity search."""
    doc_type = state.get("doc_type") or ""
    session = get_session()

    query_text = json.dumps(state.get("extracted_fields") or {})
    similar = similarity_search(session, embed(query_text, task_type="RETRIEVAL_QUERY"), top_k=3, doc_type=doc_type)
    context = "\n".join(f"Example: {r.chunk_text}" for r in similar) or None

    raw_bytes = state.get("raw_bytes") or get_object_store().get(state["object_key"])
    mime_type = mime_from_filename(state["filename"])

    result = extract(raw_bytes, mime_type, doc_type, context=context)
    validate_result = validate(doc_type, result.data)

    return {
        "extracted_fields": result.data,
        "extract_confidence": result.confidence,
        "validation_issues": validate_result.data.get("issues", []),
        "validate_confidence": validate_result.confidence,
        "retry_count": state["retry_count"] + 1,
    }
