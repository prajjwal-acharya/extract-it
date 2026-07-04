from sqlalchemy.orm import Session

from db.models import Document, DocumentEmbedding


def upsert_embedding(
    session: Session,
    document_id: str,
    chunk_index: int,
    chunk_text: str,
    embedding: list[float],
    source: str | None = None,
) -> None:
    """Insert or update a DocumentEmbedding row for the given chunk.

    DocumentEmbedding.id is a UUID PK, so merge() can't match on
    (document_id, chunk_index) — query first and update in place instead.
    """
    existing = (
        session.query(DocumentEmbedding)
        .filter_by(document_id=document_id, chunk_index=chunk_index)
        .first()
    )
    if existing:
        existing.chunk_text = chunk_text
        existing.embedding = embedding
        if source is not None:
            existing.source = source
    else:
        session.add(
            DocumentEmbedding(
                document_id=document_id,
                chunk_index=chunk_index,
                chunk_text=chunk_text,
                embedding=embedding,
                source=source,
            )
        )
    session.commit()


def similarity_search(
    session: Session,
    query_embedding: list[float],
    top_k: int = 5,
    doc_type: str | None = None,
) -> list[tuple[DocumentEmbedding, float]]:
    """Top-k (DocumentEmbedding, cosine_distance) tuples, optionally filtered by doc_type."""
    distance_col = DocumentEmbedding.embedding.cosine_distance(query_embedding)
    q = session.query(DocumentEmbedding).add_columns(distance_col)
    if doc_type is not None:
        q = q.join(Document).filter(Document.doc_type == doc_type)
    return list(q.order_by(distance_col).limit(top_k).all())
