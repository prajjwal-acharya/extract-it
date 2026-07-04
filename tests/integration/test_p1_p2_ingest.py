"""Integration seam P1↔P2: ingestion hands off to the pipeline trigger."""

import os
import tempfile
import unittest.mock as mock

import pytest

from db.models import Document
from io_pipeline.ingestion import ingest_file
from io_pipeline.validation import ValidatedFile


def _make_temp_file(name: str, content: bytes = b"%PDF-1.4 test") -> str:
    d = tempfile.mkdtemp()
    path = os.path.join(d, name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _fake_validate(data: bytes, filename: str) -> ValidatedFile:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "pdf"
    return ValidatedFile(data=data, mime_type="application/pdf", file_size=len(data), extension=ext)


@pytest.mark.skip(
    reason="deferred since P1; unblockable now that build_graph() exists, needs real implementation"
)
def test_ingest_file_enqueues_pipeline_run() -> None:
    raise NotImplementedError


def test_ingest_file_object_is_readable_by_pipeline(minio_client, postgres_session) -> None:
    content = b"%PDF-1.4 readable test"
    path = _make_temp_file("passport_RDR001_20240701.pdf", content)
    try:
        with (
            mock.patch("io_pipeline.orchestrator.get_object_store", return_value=minio_client),
            mock.patch("io_pipeline.orchestrator.get_session", return_value=postgres_session),
            mock.patch("io_pipeline.orchestrator.ValidationService") as MockVS,
        ):
            MockVS.return_value.validate.side_effect = _fake_validate
            doc_id = ingest_file(path)

        postgres_session.expire_all()
        doc = postgres_session.get(Document, doc_id)
        assert doc is not None
        retrieved = minio_client.get(doc.object_key)
        assert retrieved == content
    finally:
        os.unlink(path)


def test_ingest_creates_document_with_queued_status(minio_client, postgres_session) -> None:
    path = _make_temp_file("invoice_INV999_20240801.pdf")
    try:
        with (
            mock.patch("io_pipeline.orchestrator.get_object_store", return_value=minio_client),
            mock.patch("io_pipeline.orchestrator.get_session", return_value=postgres_session),
            mock.patch("io_pipeline.orchestrator.ValidationService") as MockVS,
        ):
            MockVS.return_value.validate.side_effect = _fake_validate
            doc_id = ingest_file(path)

        postgres_session.expire_all()
        doc = postgres_session.get(Document, doc_id)
        assert doc is not None
        assert doc.status == "queued"
    finally:
        os.unlink(path)
