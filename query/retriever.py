from db.session import get_session
from db.vector_store import similarity_search


def retrieve(query_embedding: list[float], top_k: int = 5) -> list[dict]:
    """Return the top-k document chunks ranked by cosine similarity to query_embedding."""
    session = get_session()
    rows = similarity_search(session, query_embedding, top_k=top_k)
    return [
        {"document_id": r.document_id, "chunk_text": r.chunk_text, "chunk_index": r.chunk_index}
        for r in rows
    ]
