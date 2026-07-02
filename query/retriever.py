from db.session import get_session
from db.vector_store import similarity_search
from db.models import DocumentEmbedding


def retrieve(query_embedding: list[float], top_k: int = 5) -> list[dict]:
    session = get_session()
    try:
        results = similarity_search(session, query_embedding, top_k=top_k)
        return [
            {
                "document_id": r.document_id,
                "chunk_text": r.chunk_text,
                "chunk_index": r.chunk_index,
            }
            for r in results
        ]
    finally:
        session.close()
