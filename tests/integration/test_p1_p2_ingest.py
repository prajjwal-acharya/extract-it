"""Integration seam P1↔P2: ingestion hands off to the pipeline trigger."""

import os
import tempfile
import unittest.mock as mock

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


def test_ingest_file_enqueues_pipeline_run(minio_client, postgres_session) -> None:
    """Ingesting a file via the orchestrator with a dispatch_fn must call
    that dispatch_fn exactly once with (document_id, safe_filename, object_key)."""
    import tempfile
    from io_pipeline.orchestrator import IngestionOrchestrator
    from io_pipeline.validation import ValidationService

    content = b"%PDF-1.4 dispatch test"
    with tempfile.NamedTemporaryFile(
        suffix=".pdf", prefix="passport_RDR002_20240701", delete=False
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    dispatch = mock.MagicMock()

    mock_validated = mock.MagicMock()
    mock_validated.data = content
    mock_validated.mime_type = "application/pdf"
    mock_validated.file_size = len(content)
    mock_validated.extension = "pdf"

    mock_validator = mock.MagicMock(spec=ValidationService)
    mock_validator.validate.return_value = mock_validated

    try:
        with (
            mock.patch(
                "io_pipeline.orchestrator.get_session",
                return_value=postgres_session,
            ),
        ):
            orch = IngestionOrchestrator(
                validator=mock_validator,
                store=minio_client,
                dispatch_fn=dispatch,
            )
            doc_id, is_dup = orch.ingest(content, os.path.basename(tmp_path))
    finally:
        os.unlink(tmp_path)

    assert is_dup is False
    dispatch.assert_called_once()
    call_args = dispatch.call_args[0]
    assert call_args[0] == doc_id
    assert call_args[2].startswith("raw/")


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
