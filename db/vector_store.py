from sqlalchemy.orm import Session


def upsert_embedding(
    session: Session,
    document_id: str,
    chunk_index: int,
    chunk_text: str,
    embedding: list[float],
) -> None:
    """Insert or update a DocumentEmbedding row for the given chunk."""
    raise NotImplementedError


def similarity_search(
    session: Session,
    query_embedding: list[float],
    top_k: int = 5,
) -> list:
    """Return the top-k DocumentEmbedding rows ranked by cosine distance to query_embedding."""
    raise NotImplementedError
