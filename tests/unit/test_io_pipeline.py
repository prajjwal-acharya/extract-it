import os
import tempfile
import unittest.mock as mock


from io_pipeline.ingestion import ingest_file


def _make_temp_file(name: str, content: bytes = b"%PDF-1.4 test") -> str:
    d = tempfile.mkdtemp()
    path = os.path.join(d, name)
    with open(path, "wb") as f:
        f.write(content)
    return path


def test_ingest_file_stores_object_and_creates_db_row(
    minio_client, postgres_session
) -> None:
    path = _make_temp_file("invoice_ABC123_20240101.pdf")
    try:
        with (
            mock.patch("io_pipeline.ingestion.get_object_store", return_value=minio_client),
            mock.patch("io_pipeline.ingestion.get_session", return_value=postgres_session),
        ):
            doc_id = ingest_file(path)

        from db.models import Document
        doc = postgres_session.get(Document, doc_id)
        assert doc is not None
        assert doc.status == "queued"
        assert doc.object_key == "raw/invoice_ABC123_20240101.pdf"
    finally:
        os.unlink(path)


def test_ingest_file_parses_doc_type_from_filename(
    minio_client, postgres_session
) -> None:
    path = _make_temp_file("bank_statement_XYZ_20231215.pdf")
    try:
        with (
            mock.patch("io_pipeline.ingestion.get_object_store", return_value=minio_client),
            mock.patch("io_pipeline.ingestion.get_session", return_value=postgres_session),
        ):
            doc_id = ingest_file(path)

        from db.models import Document
        doc = postgres_session.get(Document, doc_id)
        assert doc is not None
        assert doc.doc_type == "bank_statement"
    finally:
        os.unlink(path)


def test_ingest_file_returns_document_id_string(
    minio_client, postgres_session
) -> None:
    path = _make_temp_file("passport_P1234567_20240601.pdf")
    try:
        with (
            mock.patch("io_pipeline.ingestion.get_object_store", return_value=minio_client),
            mock.patch("io_pipeline.ingestion.get_session", return_value=postgres_session),
        ):
            doc_id = ingest_file(path)
        assert isinstance(doc_id, str)
        assert len(doc_id) > 0
    finally:
        os.unlink(path)


def test_write_output_updates_document_row() -> None:
    raise NotImplementedError


def test_write_output_appends_confidence_log() -> None:
    raise NotImplementedError


def test_write_output_writes_json_to_object_store() -> None:
    raise NotImplementedError
