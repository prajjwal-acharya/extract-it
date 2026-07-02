"""Integration seam P1↔P2: ingestion hands off to the pipeline trigger."""
import os
import tempfile
import unittest.mock as mock

from db.models import Document
from io_pipeline.ingestion import ingest_file


def _make_temp_file(name: str, content: bytes = b"%PDF-1.4 test") -> str:
    d = tempfile.mkdtemp()
    path = os.path.join(d, name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def test_ingest_file_enqueues_pipeline_run() -> None:
    raise NotImplementedError


def test_ingest_file_object_is_readable_by_pipeline(
    minio_client, postgres_session
) -> None:
    content = b"%PDF-1.4 readable test"
    path = _make_temp_file("passport_RDR001_20240701.pdf", content)
    try:
        with (
            mock.patch("io_pipeline.ingestion.get_object_store", return_value=minio_client),
            mock.patch("io_pipeline.ingestion.get_session", return_value=postgres_session),
        ):
            doc_id = ingest_file(path)

        doc = postgres_session.get(Document, doc_id)
        assert doc is not None
        retrieved = minio_client.get(doc.object_key)
        assert retrieved == content
    finally:
        os.unlink(path)


def test_ingest_creates_document_with_queued_status(
    minio_client, postgres_session
) -> None:
    path = _make_temp_file("invoice_INV999_20240801.pdf")
    try:
        with (
            mock.patch("io_pipeline.ingestion.get_object_store", return_value=minio_client),
            mock.patch("io_pipeline.ingestion.get_session", return_value=postgres_session),
        ):
            doc_id = ingest_file(path)

        doc = postgres_session.get(Document, doc_id)
        assert doc is not None
        assert doc.status == "queued"
    finally:
        os.unlink(path)
