"""Regression tests for Phase 1 atomicity, idempotency, and resource management."""

from __future__ import annotations

import threading
import unittest.mock as mock

import pytest
from sqlalchemy.exc import IntegrityError

from io_pipeline.orchestrator import IngestionOrchestrator
from io_pipeline.validation import ValidatedFile, ValidationError

VALID_DATA = b"%PDF-1.4 hardening"
VALID_FILENAME = "invoice_HARD001_20240101.pdf"
_VALIDATED = ValidatedFile(
    data=VALID_DATA,
    mime_type="application/pdf",
    file_size=len(VALID_DATA),
    extension="pdf",
)


def _make_orch(*, commit_error=None, store=None):
    validator = mock.MagicMock()
    validator.validate.return_value = _VALIDATED
    if store is None:
        store = mock.MagicMock()
    session = mock.MagicMock()
    session.query.return_value.filter.return_value.first.return_value = None
    if commit_error is not None:
        session.commit.side_effect = commit_error
    orch = IngestionOrchestrator(validator=validator, store=store, dispatch_fn=None)
    return orch, store, session


# P1: orphan cleanup
def test_db_failure_after_put_triggers_delete():
    orch, store, session = _make_orch(commit_error=RuntimeError("DB down"))
    with mock.patch("io_pipeline.orchestrator.get_session", return_value=session):
        with pytest.raises(RuntimeError, match="DB down"):
            orch.ingest(VALID_DATA, VALID_FILENAME)
    store.delete.assert_called_once()
    deleted_key = store.delete.call_args[0][0]
    assert deleted_key.startswith("raw/") and deleted_key.endswith(".pdf")


def test_minio_failure_never_calls_db():
    store = mock.MagicMock()
    store.put.side_effect = RuntimeError("MinIO down")
    orch, _, session = _make_orch(store=store)
    with mock.patch("io_pipeline.orchestrator.get_session", return_value=session):
        with pytest.raises(RuntimeError, match="MinIO down"):
            orch.ingest(VALID_DATA, VALID_FILENAME)
    session.add.assert_not_called()
    store.delete.assert_not_called()


def test_orphan_cleanup_suppresses_delete_error_and_reraises_original(caplog):
    import logging

    store = mock.MagicMock()
    store.delete.side_effect = RuntimeError("MinIO also down")
    orch, _, session = _make_orch(commit_error=RuntimeError("DB down"), store=store)
    with mock.patch("io_pipeline.orchestrator.get_session", return_value=session):
        with caplog.at_level(logging.ERROR, logger="io_pipeline.orchestrator"):
            with pytest.raises(RuntimeError, match="DB down"):
                orch.ingest(VALID_DATA, VALID_FILENAME)
    assert any("OrphanCleanupFailed" in r.message for r in caplog.records)


# P2: concurrent idempotency
def test_integrity_error_returns_existing_id_and_deletes_orphan():
    existing_id = "race-winner-uuid"
    winner = mock.MagicMock()
    winner.id = existing_id
    orch, store, session = _make_orch(
        commit_error=IntegrityError("unique", {}, Exception("unique"))
    )
    # first .first() → None (find_by_hash), second → winner (post-IntegrityError re-query)
    session.query.return_value.filter.return_value.first.side_effect = [None, winner]
    with mock.patch("io_pipeline.orchestrator.get_session", return_value=session):
        doc_id, is_dup = orch.ingest(VALID_DATA, VALID_FILENAME)
    assert doc_id == existing_id
    assert is_dup is True
    store.delete.assert_called_once()


def test_validation_error_never_reaches_storage():
    validator = mock.MagicMock()
    validator.validate.side_effect = ValidationError("empty_file")
    store = mock.MagicMock()
    orch = IngestionOrchestrator(validator=validator, store=store)
    with pytest.raises(ValidationError):
        orch.ingest(b"", "doc.pdf")
    store.put.assert_not_called()
    store.delete.assert_not_called()


# P3: fitz resource cleanup
def test_fitz_document_closed_on_validation_success():
    import fitz

    real = fitz.open()
    real.new_page()
    pdf_bytes = real.tobytes()
    real.close()
    mock_doc = mock.MagicMock()
    mock_doc.needs_pass = False
    mock_doc.__len__ = lambda self: 1
    with mock.patch("io_pipeline.validation.fitz.open", return_value=mock_doc):
        from io_pipeline.validation import ValidationService

        ValidationService().validate(pdf_bytes, "test.pdf")
    mock_doc.close.assert_called_once()


def test_fitz_document_closed_on_validation_error():
    import fitz

    real = fitz.open()
    real.new_page()
    pdf_bytes = real.tobytes()
    real.close()
    mock_doc = mock.MagicMock()
    mock_doc.needs_pass = True
    with mock.patch("io_pipeline.validation.fitz.open", return_value=mock_doc):
        from io_pipeline.validation import ValidationService, ValidationError

        with pytest.raises(ValidationError, match="pdf_password_protected"):
            ValidationService().validate(pdf_bytes, "test.pdf")
    mock_doc.close.assert_called_once()


# P4: watcher debounce
def test_watcher_settle_waits_for_stable_size():
    from adapters.trigger.local_watch import _NewFileHandler

    calls = []
    handler = _NewFileHandler(calls.append, settle_secs=0.01)
    # sizes: 100, 200, 200 → second stable → calls callback
    with mock.patch("adapters.trigger.local_watch.os.path.getsize", side_effect=[100, 200, 200]):
        with mock.patch("adapters.trigger.local_watch.time.sleep"):
            handler._settle_and_call("/fake/test.pdf")
    assert calls == ["/fake/test.pdf"]


def test_watcher_settle_skips_disappeared_file():
    from adapters.trigger.local_watch import _NewFileHandler

    calls = []
    handler = _NewFileHandler(calls.append, settle_secs=0.01)
    with mock.patch("adapters.trigger.local_watch.os.path.getsize", side_effect=OSError("gone")):
        with mock.patch("adapters.trigger.local_watch.time.sleep"):
            handler._settle_and_call("/fake/ghost.pdf")
    assert calls == []
