from sqlalchemy.orm import Session
from db.models import DocumentEmbedding


def upsert_embedding(session: Session, document_id: str, chunk_index: int, chunk_text: str, embedding: list[float]) -> None:
    existing = session.query(DocumentEmbedding).filter_by(document_id=document_id, chunk_index=chunk_index).first()
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


def similarity_search(session: Session, query_embedding: list[float], top_k: int = 5) -> list[DocumentEmbedding]:
    return (
        session.query(DocumentEmbedding)
        .order_by(DocumentEmbedding.embedding.cosine_distance(query_embedding))
        .limit(top_k)
        .all()
    )
