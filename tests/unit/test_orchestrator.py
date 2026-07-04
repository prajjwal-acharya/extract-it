from __future__ import annotations

import hashlib
import unittest.mock as mock

import pytest

from io_pipeline.orchestrator import IngestionOrchestrator
from io_pipeline.validation import ValidatedFile, ValidationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_PDF_DATA = b"%PDF-1.4 minimal"
VALID_PDF_FILENAME = "passport_ABC_20240101.pdf"

# Pre-computed so tests don't depend on hashing internals
VALID_PDF_HASH = hashlib.sha256(VALID_PDF_DATA).hexdigest()

_VALIDATED = ValidatedFile(
    data=VALID_PDF_DATA,
    mime_type="application/pdf",
    file_size=len(VALID_PDF_DATA),
    extension="pdf",
)


def _make_orchestrator(
    *,
    validated_file: ValidatedFile = _VALIDATED,
    existing_id: str | None = None,
    dispatch_fn=None,
):
    """Return orchestrator with all I/O mocked."""
    validator = mock.MagicMock()
    validator.validate.return_value = validated_file

    store = mock.MagicMock()

    mock_session = mock.MagicMock()
    # _find_by_hash query result
    find_result = mock.MagicMock()
    find_result.id = existing_id
    mock_session.query.return_value.filter.return_value.first.return_value = (
        find_result if existing_id else None
    )

    orch = IngestionOrchestrator(
        validator=validator,
        store=store,
        dispatch_fn=dispatch_fn,
    )
    return orch, validator, store, mock_session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path_returns_uuid_and_not_duplicate():
    dispatch = mock.MagicMock()
    orch, validator, store, mock_session = _make_orchestrator(dispatch_fn=dispatch)

    with mock.patch("io_pipeline.orchestrator.get_session", return_value=mock_session):
        doc_id, is_dup = orch.ingest(VALID_PDF_DATA, VALID_PDF_FILENAME)

    assert is_dup is False
    assert len(doc_id) == 36  # UUID4 format
    assert doc_id.count("-") == 4


def test_happy_path_object_key_uses_uuid_not_filename():
    dispatch = mock.MagicMock()
    orch, _, store, mock_session = _make_orchestrator(dispatch_fn=dispatch)

    with mock.patch("io_pipeline.orchestrator.get_session", return_value=mock_session):
        doc_id, _ = orch.ingest(VALID_PDF_DATA, VALID_PDF_FILENAME)

    put_call = store.put.call_args
    key = put_call[0][0]  # first positional arg
    assert key == f"raw/{doc_id}.pdf"
    assert "passport" not in key  # original filename must not leak into key


def test_happy_path_dispatch_called_once():
    dispatch = mock.MagicMock()
    orch, _, _, mock_session = _make_orchestrator(dispatch_fn=dispatch)

    with mock.patch("io_pipeline.orchestrator.get_session", return_value=mock_session):
        doc_id, _ = orch.ingest(VALID_PDF_DATA, VALID_PDF_FILENAME)

    dispatch.assert_called_once()
    call_args = dispatch.call_args[0]
    assert call_args[0] == doc_id
    assert call_args[2].startswith("raw/")


def test_duplicate_hash_returns_existing_id_no_store_write():
    existing = "aaaabbbb-1234-5678-abcd-000000000001"
    orch, _, store, mock_session = _make_orchestrator(existing_id=existing)

    with mock.patch("io_pipeline.orchestrator.get_session", return_value=mock_session):
        doc_id, is_dup = orch.ingest(VALID_PDF_DATA, VALID_PDF_FILENAME)

    assert doc_id == existing
    assert is_dup is True
    store.put.assert_not_called()


def test_validation_error_propagates():
    validator = mock.MagicMock()
    validator.validate.side_effect = ValidationError("empty_file")
    orch = IngestionOrchestrator(
        validator=validator,
        store=mock.MagicMock(),
        dispatch_fn=None,
    )

    with pytest.raises(ValidationError) as exc_info:
        orch.ingest(b"", "doc.pdf")

    assert exc_info.value.reason == "empty_file"


def test_no_dispatch_when_dispatch_fn_is_none():
    # dispatch_fn=None should not raise — orchestrator skips dispatch silently
    orch, _, _, mock_session = _make_orchestrator(dispatch_fn=None)

    with mock.patch("io_pipeline.orchestrator.get_session", return_value=mock_session):
        doc_id, is_dup = orch.ingest(VALID_PDF_DATA, VALID_PDF_FILENAME)

    assert is_dup is False  # completed normally without dispatching


def test_bootstrap_document_has_correct_fields():
    dispatch = mock.MagicMock()
    orch, _, _, mock_session = _make_orchestrator(dispatch_fn=dispatch)

    with mock.patch("io_pipeline.orchestrator.get_session", return_value=mock_session):
        doc_id, _ = orch.ingest(VALID_PDF_DATA, VALID_PDF_FILENAME)

    # session.add() was called with a Document — inspect it
    add_call = mock_session.add.call_args[0][0]
    assert add_call.hash == VALID_PDF_HASH
    assert add_call.file_size == len(VALID_PDF_DATA)
    assert add_call.mime_type == "application/pdf"
    assert add_call.current_phase == "ingested"
    assert add_call.status == "queued"
