from sqlalchemy.orm import Session

from db.models import Document, DocumentEmbedding


def upsert_embedding(
    session: Session,
    document_id: str,
    chunk_index: int,
    chunk_text: str,
    embedding: list[float],
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
    else:
        session.add(DocumentEmbedding(
            document_id=document_id,
            chunk_index=chunk_index,
            chunk_text=chunk_text,
            embedding=embedding,
        ))
    session.commit()


def similarity_search(
    session: Session,
    query_embedding: list[float],
    top_k: int = 5,
    doc_type: str | None = None,
) -> list:
    """Top-k DocumentEmbedding rows by cosine distance, optionally filtered by doc_type."""
    q = session.query(DocumentEmbedding)
    if doc_type is not None:
        q = q.join(Document).filter(Document.doc_type == doc_type)
    return (
        q.order_by(DocumentEmbedding.embedding.cosine_distance(query_embedding))
        .limit(top_k)
        .all()
    )
