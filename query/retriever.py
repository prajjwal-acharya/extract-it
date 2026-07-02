def retrieve(query_embedding: list[float], top_k: int = 5) -> list[dict]:
    """Return the top-k document chunks ranked by cosine similarity to query_embedding.

    Each result dict contains: document_id, chunk_text, chunk_index.
    """
    raise NotImplementedError
