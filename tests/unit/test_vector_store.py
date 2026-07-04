"""Unit tests for db/vector_store.py — upsert and similarity search."""

import math
import uuid


def _make_embedding(seed: float, dims: int = 768) -> list[float]:
    """Return a normalised unit vector for deterministic test embeddings."""
    v = [seed + i * 0.001 for i in range(dims)]
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


def _make_doc(session, doc_type: str) -> str:
    from db.models import Document

    doc_id = str(uuid.uuid4())
    doc = Document(
        id=doc_id,
        filename=f"{doc_type}_{doc_id[:8]}.pdf",
        object_key=f"raw/{doc_type}_{doc_id[:8]}.pdf",
        doc_type=doc_type,
        status="completed",
    )
    session.add(doc)
    session.commit()
    return doc_id


def test_upsert_embedding_inserts_new_row(postgres_session) -> None:
    from db.models import DocumentEmbedding
    from db.vector_store import upsert_embedding

    dtype = f"passport_insert_{uuid.uuid4().hex[:6]}"
    doc_id = _make_doc(postgres_session, doc_type=dtype)
    embedding = _make_embedding(1.0)

    upsert_embedding(postgres_session, doc_id, 0, "test chunk", embedding)

    row = (
        postgres_session.query(DocumentEmbedding).filter_by(document_id=doc_id, chunk_index=0).one()
    )
    assert row.chunk_text == "test chunk"
    assert len(row.embedding) == 768


def test_upsert_embedding_updates_existing_row(postgres_session) -> None:
    from db.models import DocumentEmbedding
    from db.vector_store import upsert_embedding

    dtype = f"passport_update_{uuid.uuid4().hex[:6]}"
    doc_id = _make_doc(postgres_session, doc_type=dtype)

    upsert_embedding(postgres_session, doc_id, 0, "original", _make_embedding(1.0))
    upsert_embedding(postgres_session, doc_id, 0, "updated", _make_embedding(2.0))

    postgres_session.expire_all()
    rows = (
        postgres_session.query(DocumentEmbedding).filter_by(document_id=doc_id, chunk_index=0).all()
    )
    assert len(rows) == 1
    assert rows[0].chunk_text == "updated"


def test_similarity_search_orders_by_distance(postgres_session) -> None:
    from db.vector_store import similarity_search, upsert_embedding

    # Unique doc_type so session-scoped fixture rows don't contaminate results
    dtype = f"order_test_{uuid.uuid4().hex[:6]}"
    doc_a = _make_doc(postgres_session, doc_type=dtype)
    doc_b = _make_doc(postgres_session, doc_type=dtype)

    embedding_a = _make_embedding(1.0)  # matches the query
    embedding_b = _make_embedding(100.0)  # far from the query

    upsert_embedding(postgres_session, doc_a, 0, "close", embedding_a)
    upsert_embedding(postgres_session, doc_b, 0, "far", embedding_b)

    query = _make_embedding(1.0)
    results = similarity_search(postgres_session, query, top_k=2, doc_type=dtype)
    # results is list[tuple[DocumentEmbedding, float]]
    assert results[0][0].chunk_text == "close"
    assert results[1][0].chunk_text == "far"
    # distances should be non-negative floats in order
    assert results[0][1] <= results[1][1]


def test_similarity_search_filters_by_doc_type(postgres_session) -> None:
    from db.vector_store import similarity_search, upsert_embedding

    # Unique suffixes guarantee no cross-test contamination
    passport_type = f"passport_filter_{uuid.uuid4().hex[:6]}"
    bank_type = f"bank_filter_{uuid.uuid4().hex[:6]}"

    passport_id = _make_doc(postgres_session, doc_type=passport_type)
    bank_id = _make_doc(postgres_session, doc_type=bank_type)

    embedding = _make_embedding(5.0)
    upsert_embedding(postgres_session, passport_id, 0, "passport chunk", embedding)
    upsert_embedding(postgres_session, bank_id, 0, "bank chunk", embedding)

    results = similarity_search(postgres_session, embedding, top_k=10, doc_type=passport_type)
    assert len(results) >= 1
    doc_types = {row.document.doc_type for row, _ in results}
    assert doc_types == {passport_type}
    assert all(row.chunk_text == "passport chunk" for row, _ in results)
