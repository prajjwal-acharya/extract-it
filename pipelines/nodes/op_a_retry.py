from agents import extract_agent, validate_agent
from db.session import get_session
from db.vector_store import similarity_search
from pipelines.state import DocumentState


def op_a_retry_node(state: DocumentState) -> dict:
    session = get_session()
    try:
        query_embedding = [0.0] * 768  # placeholder — real embed in P7
        similar = similarity_search(session, query_embedding, top_k=3)
        rag_context = "\n".join(e.chunk_text for e in similar)
        augmented_content = f"{rag_context}\n\n{state.raw_content}"
    finally:
        session.close()

    result = extract_agent.extract(augmented_content, state.doc_type or "")
    val = validate_agent.validate(state.doc_type or "", result.data)
    return {
        "extracted_fields": result.data,
        "extract_confidence": result.confidence,
        "validation_issues": val.data.get("issues", []),
        "validate_confidence": val.confidence,
        "retry_count": state.retry_count + 1,
    }
