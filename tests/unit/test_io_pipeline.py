import json
import os
import tempfile
import unittest.mock as mock
import uuid

import pytest

from db.models import ConfidenceLog, Document
from io_pipeline.ingestion import ingest_file
from io_pipeline.output_writer import write_output


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


def _make_doc(session, doc_id: str) -> Document:
    doc = Document(
        id=doc_id,
        filename="passport_P001_20240101.pdf",
        object_key="raw/passport_P001_20240101.pdf",
        status="queued",
    )
    session.add(doc)
    session.commit()
    return doc


def _make_state(doc_id: str) -> dict:
    return {
        "document_id": doc_id,
        "universal_schema": {"holder_name": "JOHN SMITH", "id_number": "AB123456", "expiry_date": "2030-01-01"},
        "classify_confidence": 0.95,
        "extract_confidence": 0.88,
        "validate_confidence": 0.92,
        "validation_issues": [],
        "error": None,
        "hitl_required": False,
        "hitl_approved": None,
    }


def test_write_output_updates_document_row(minio_client, postgres_session) -> None:
    doc_id = str(uuid.uuid4())
    _make_doc(postgres_session, doc_id)
    state = _make_state(doc_id)

    with (
        mock.patch("io_pipeline.output_writer.get_session", return_value=postgres_session),
        mock.patch("io_pipeline.output_writer.get_object_store", return_value=minio_client),
    ):
        write_output(state)  # type: ignore[arg-type]

    postgres_session.expire_all()
    doc = postgres_session.get(Document, doc_id)
    assert doc.status == "completed"
    assert doc.universal_schema == state["universal_schema"]


def test_write_output_appends_confidence_log(minio_client, postgres_session) -> None:
    doc_id = str(uuid.uuid4())
    _make_doc(postgres_session, doc_id)
    state = _make_state(doc_id)

    with (
        mock.patch("io_pipeline.output_writer.get_session", return_value=postgres_session),
        mock.patch("io_pipeline.output_writer.get_object_store", return_value=minio_client),
    ):
        write_output(state)  # type: ignore[arg-type]

    logs = (
        postgres_session.query(ConfidenceLog)
        .filter(ConfidenceLog.document_id == doc_id)
        .all()
    )
    agents = {log.agent for log in logs}
    assert agents == {"classify", "extract", "validate"}
    scores = {log.agent: log.score for log in logs}
    assert scores["classify"] == pytest.approx(0.95)
    assert scores["extract"] == pytest.approx(0.88)
    assert scores["validate"] == pytest.approx(0.92)


def test_write_output_writes_json_to_object_store(minio_client, postgres_session) -> None:
    doc_id = str(uuid.uuid4())
    _make_doc(postgres_session, doc_id)
    state = _make_state(doc_id)

    with (
        mock.patch("io_pipeline.output_writer.get_session", return_value=postgres_session),
        mock.patch("io_pipeline.output_writer.get_object_store", return_value=minio_client),
    ):
        write_output(state)  # type: ignore[arg-type]

    stored = json.loads(minio_client.get(f"output/{doc_id}.json"))
    assert stored == state["universal_schema"]
