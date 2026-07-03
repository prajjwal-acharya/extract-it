from agents.extract_agent import extract
from agents.llm_client import embed
from db.session import get_session
from db.vector_store import similarity_search
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename


def extract_node(state: GraphState) -> dict:
    """Run extract agent with RAG context from pgvector, return GraphState update."""
    doc_type = state.get("doc_type") or ""
    mime_type = mime_from_filename(state["filename"])

    session = get_session()
    similar = similarity_search(session, embed(doc_type or "document", task_type="RETRIEVAL_QUERY"), top_k=3, doc_type=doc_type or None)
    context = "\n".join(f"Example: {r.chunk_text}" for r in similar) or None

    result = extract(state["raw_bytes"], mime_type, doc_type, context=context)
    update: dict = {
        "extracted_fields": result.data,
        "extract_confidence": result.confidence,
        "tool_call_count": result.tool_calls_made,
        "verification_passed": result.verification_passed,
    }
    if not result.success:
        update["error"] = result.reason
    return update
