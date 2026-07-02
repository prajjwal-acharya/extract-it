import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.deps import get_db
from api.main import app


@pytest.fixture
def client(postgres_session):
    """TestClient with DB dependency overridden to use test session."""
    app.dependency_overrides[get_db] = lambda: postgres_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_endpoint_returns_ok(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ingest_endpoint_accepts_pdf_upload(client, sample_pdf_bytes, minio_client) -> None:
    with mock.patch("api.routes.ingest.ingest_file", return_value="test-doc-id-123"):
        resp = client.post(
            "/ingest/",
            files={"file": ("passport_P001_20240101.pdf", sample_pdf_bytes, "application/pdf")},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "document_id" in data
    assert data["document_id"] == "test-doc-id-123"


def test_ingest_endpoint_rejects_missing_file(client) -> None:
    resp = client.post("/ingest/")
    assert resp.status_code == 422


def test_query_endpoint_returns_answer_and_sources() -> None:
    raise NotImplementedError


def test_query_endpoint_rejects_empty_question() -> None:
    raise NotImplementedError


def test_get_db_dependency_yields_session() -> None:
    gen = get_db()
    db = next(gen)
    assert isinstance(db, Session)
    try:
        next(gen)
    except StopIteration:
        pass
