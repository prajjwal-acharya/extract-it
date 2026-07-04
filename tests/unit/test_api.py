import os
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


def test_ingest_rejects_path_traversal_filename(client, sample_pdf_bytes) -> None:
    """CWE-22: directory components in the filename must not escape the temp dir."""
    malicious_names = [
        "../../../etc/cron.d/evil",
        "/etc/passwd",
        "..\\..\\windows\\system32\\evil.exe",
    ]
    import tempfile

    tmp_dir = tempfile.gettempdir()

    for name in malicious_names:
        with mock.patch("api.routes.ingest.ingest_file", return_value="safe-id") as m:
            resp = client.post(
                "/ingest/",
                files={"file": (name, sample_pdf_bytes, "application/pdf")},
            )
        assert resp.status_code == 200, f"Unexpected status for filename {name!r}"
        called_path: str = m.call_args[0][0]
        # The file must land inside the temp directory, not at an arbitrary path.
        assert os.path.dirname(called_path) == tmp_dir, (
            f"Path traversal: {called_path!r} escaped temp dir for filename {name!r}"
        )


def test_ingest_rejects_oversized_upload(client) -> None:
    """Uploads exceeding MAX_UPLOAD_BYTES (25 MB) must return 413."""
    oversized = b"x" * (25 * 1024 * 1024 + 1)
    resp = client.post(
        "/ingest/",
        files={"file": ("big.pdf", oversized, "application/pdf")},
    )
    assert resp.status_code == 413


def test_query_endpoint_returns_answer_and_sources(client) -> None:
    with (
        mock.patch("api.routes.query.embed", return_value=[0.1] * 768),
        mock.patch(
            "api.routes.query.retrieve",
            return_value=[
                {"document_id": "doc-1", "chunk_text": '{"surname": "SMITH"}', "chunk_index": 0}
            ],
        ),
        mock.patch(
            "api.routes.query.synthesize", return_value="The holder is SMITH [Document doc-1]."
        ),
    ):
        resp = client.post("/query/", json={"question": "Who is the passport holder?"})

    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "sources" in data
    assert "doc-1" in data["sources"]


def test_query_endpoint_rejects_empty_question(client) -> None:
    resp = client.post("/query/", json={"question": ""})
    assert resp.status_code == 422


def test_get_db_dependency_yields_session() -> None:
    gen = get_db()
    db = next(gen)
    assert isinstance(db, Session)
    try:
        next(gen)
    except StopIteration:
        pass
