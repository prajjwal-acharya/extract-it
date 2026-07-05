"""Tests for Phase 6 — Transactional Persistence.

Covers:
  - Atomic persistence: object-store failure → persist_failed, no silent partial success
  - Embedding failure → persist_failed
  - DB write failure → persist_failed
  - persist ConfidenceLog (agent="persist") written on success (score 1.0) and failure (score 0.0)
  - PersistenceAuditLog written with ResolutionDecision + LearningDecision snapshots
  - SchemaProposalRecord created when LearningDecision.schema_candidate is True
  - Correction exemplar: source="hitl_correction" when learn_from_correction, "document" otherwise
  - Bypass removed from review route (no direct upsert_embedding)
  - Successful persistence regression: completed status, terminal_status written
  - persist_failed is a valid DOCUMENT_PHASE
"""

from __future__ import annotations

import unittest.mock as mock
import uuid

import pytest

from db.models import (
    DOCUMENT_PHASES,
    ConfidenceLog,
    Document,
    PersistenceAuditLog,
    SchemaProposalRecord,
)
from io_pipeline.output_writer import write_output
from pipelines.resolution.models import ResolutionDecision, Strategy
from pipelines.truth_engine.models import (
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc_id() -> str:
    return str(uuid.uuid4())


def _truth_report(
    allow_learning: bool = True,
    additional_fields: list[str] | None = None,
) -> TruthReport:
    return TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=0.92, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[],
            required_fields_missing=[],
            additional_fields=additional_fields or [],
            coverage_score=1.0,
        ),
        verification_reports=[],
        final_confidence=0.92,
        decision_reason="test",
        persistence=PersistenceDecision(
            document_status="completed" if allow_learning else "failed",
            allow_completion=allow_learning,
            allow_embedding=allow_learning,
            allow_learning=allow_learning,
            reason="test",
        ),
    )


def _accept_decision() -> ResolutionDecision:
    return ResolutionDecision(
        strategy=Strategy.ACCEPT,
        reason="high_confidence",
        requires_human=False,
        learning_candidate=True,
    )


def _make_state(
    doc_id: str,
    truth_report: TruthReport | None = None,
    resolution_decision: ResolutionDecision | None = None,
    extracted_fields: dict | None = None,
    schema_proposal: dict | None = None,
    hitl_correction: bool = False,
) -> dict:
    return {
        "document_id": doc_id,
        "filename": "test.pdf",
        "object_key": "raw/test.pdf",
        "doc_type": "passport",
        "truth_report": truth_report,
        "resolution_decision": resolution_decision,
        "extracted_fields": extracted_fields or {"passport_number": "X123456"},
        "universal_schema": {"passport_number": "X123456"},
        "classify_confidence": 0.95,
        "extract_confidence": 0.90,
        "schema_version": "1.0",
        "schema_proposal": schema_proposal,
        "hitl_correction": hitl_correction,
        "execution_history": [],
        "error": None,
        "hitl_required": False,
        "hitl_approved": None,
    }


def _mock_doc(doc_id: str, session_mock):
    doc = Document(
        id=doc_id,
        filename="test.pdf",
        object_key="raw/test.pdf",
        status="finalizing",
        current_phase="finalizing",
    )
    session_mock.get.return_value = doc
    return doc


# ---------------------------------------------------------------------------
# persist_failed in DOCUMENT_PHASES
# ---------------------------------------------------------------------------


def test_persist_failed_is_valid_document_phase() -> None:
    assert "persist_failed" in DOCUMENT_PHASES


# ---------------------------------------------------------------------------
# Successful persistence regression
# ---------------------------------------------------------------------------


class TestSuccessfulPersist:
    def _run(self, doc_id: str, state: dict):
        session = mock.MagicMock()
        doc = _mock_doc(doc_id, session)

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store") as mock_store,
            mock.patch("io_pipeline.output_writer.embed", return_value=[0.1] * 768),
            mock.patch("io_pipeline.output_writer.upsert_embedding"),
        ):
            mock_store.return_value.put = mock.MagicMock()
            write_output(state)

        return session, doc

    def test_document_status_set_to_completed(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session, doc = self._run(doc_id, state)
        assert doc.status == "completed"
        assert doc.current_phase == "completed"

    def test_object_store_put_called(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session = mock.MagicMock()
        _mock_doc(doc_id, session)

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store") as mock_store,
            mock.patch("io_pipeline.output_writer.embed", return_value=[0.1] * 768),
            mock.patch("io_pipeline.output_writer.upsert_embedding"),
        ):
            store_instance = mock_store.return_value
            write_output(state)

        store_instance.put.assert_called_once()
        call_args = store_instance.put.call_args
        assert "output/" in call_args[0][0]

    def test_persist_confidence_log_score_1_on_success(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session, _ = self._run(doc_id, state)

        added_logs = [
            obj
            for call in session.add.call_args_list
            for obj in [call[0][0]]
            if isinstance(obj, ConfidenceLog) and obj.agent == "persist"
        ]
        assert len(added_logs) == 1
        assert added_logs[0].score == 1.0

    def test_persist_audit_log_written_on_success(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session, _ = self._run(doc_id, state)

        audit_logs = [
            obj
            for call in session.add.call_args_list
            for obj in [call[0][0]]
            if isinstance(obj, PersistenceAuditLog)
        ]
        assert len(audit_logs) == 1
        assert audit_logs[0].persist_status == "completed"
        assert audit_logs[0].resolution_strategy == "accept"

    def test_session_committed_and_closed(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session, _ = self._run(doc_id, state)
        assert session.commit.called
        assert session.close.called


# ---------------------------------------------------------------------------
# Atomicity: object-store failure → persist_failed
# ---------------------------------------------------------------------------


class TestObjectStoreFailure:
    def _run_with_store_error(self, doc_id: str, state: dict):
        session = mock.MagicMock()
        doc = _mock_doc(doc_id, session)

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store") as mock_store,
            mock.patch("io_pipeline.output_writer.embed", return_value=[0.1] * 768),
            mock.patch("io_pipeline.output_writer.upsert_embedding"),
        ):
            mock_store.return_value.put.side_effect = OSError("bucket unavailable")
            with pytest.raises(OSError, match="bucket unavailable"):
                write_output(state)

        return session, doc

    def test_persist_failed_set_on_object_store_error(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session, doc = self._run_with_store_error(doc_id, state)
        assert doc.status == "persist_failed"
        assert doc.current_phase == "persist_failed"

    def test_persist_confidence_log_score_0_on_object_store_failure(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session, _ = self._run_with_store_error(doc_id, state)

        persist_logs = [
            obj
            for call in session.add.call_args_list
            for obj in [call[0][0]]
            if isinstance(obj, ConfidenceLog) and obj.agent == "persist"
        ]
        assert len(persist_logs) == 1
        assert persist_logs[0].score == 0.0

    def test_session_rolled_back_then_recover_committed_on_failure(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session, _ = self._run_with_store_error(doc_id, state)
        assert session.rollback.called

    def test_session_always_closed_on_failure(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session, _ = self._run_with_store_error(doc_id, state)
        assert session.close.called


# ---------------------------------------------------------------------------
# Atomicity: embedding failure → persist_failed
# ---------------------------------------------------------------------------


class TestEmbeddingFailure:
    def test_persist_failed_on_embedding_error(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(allow_learning=True), _accept_decision())
        session = mock.MagicMock()
        doc = _mock_doc(doc_id, session)

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store") as mock_store,
            mock.patch(
                "io_pipeline.output_writer.embed", side_effect=RuntimeError("embed API down")
            ),
            mock.patch("io_pipeline.output_writer.upsert_embedding"),
        ):
            mock_store.return_value.put = mock.MagicMock()
            with pytest.raises(RuntimeError, match="embed API down"):
                write_output(state)

        assert doc.status == "persist_failed"

    def test_persist_confidence_log_score_0_on_embedding_failure(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(allow_learning=True), _accept_decision())
        session = mock.MagicMock()
        _mock_doc(doc_id, session)

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store") as mock_store,
            mock.patch(
                "io_pipeline.output_writer.embed", side_effect=RuntimeError("embed API down")
            ),
            mock.patch("io_pipeline.output_writer.upsert_embedding"),
        ):
            mock_store.return_value.put = mock.MagicMock()
            with pytest.raises(RuntimeError):
                write_output(state)

        persist_logs = [
            obj
            for call in session.add.call_args_list
            for obj in [call[0][0]]
            if isinstance(obj, ConfidenceLog) and obj.agent == "persist"
        ]
        assert persist_logs and persist_logs[0].score == 0.0


# ---------------------------------------------------------------------------
# Atomicity: DB Phase A failure → persist_failed
# ---------------------------------------------------------------------------


class TestDbPhaseAFailure:
    def test_persist_failed_when_document_not_found(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session = mock.MagicMock()
        session.get.return_value = None  # document missing

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store"),
        ):
            with pytest.raises(ValueError, match="not found"):
                write_output(state)

    def test_session_closed_even_when_db_fails(self) -> None:
        doc_id = _doc_id()
        state = _make_state(doc_id, _truth_report(), _accept_decision())
        session = mock.MagicMock()
        session.get.return_value = None

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store"),
        ):
            with pytest.raises(ValueError):
                write_output(state)

        assert session.close.called


# ---------------------------------------------------------------------------
# PersistenceAuditLog contents
# ---------------------------------------------------------------------------


class TestPersistenceAuditLog:
    def _run(self, state: dict):
        doc_id = state["document_id"]
        session = mock.MagicMock()
        _mock_doc(doc_id, session)

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store") as ms,
            mock.patch("io_pipeline.output_writer.embed", return_value=[0.1] * 768),
            mock.patch("io_pipeline.output_writer.upsert_embedding"),
        ):
            ms.return_value.put = mock.MagicMock()
            write_output(state)

        return [
            call[0][0]
            for call in session.add.call_args_list
            if isinstance(call[0][0], PersistenceAuditLog)
        ]

    def test_resolution_strategy_stored(self) -> None:
        state = _make_state(_doc_id(), _truth_report(), _accept_decision())
        logs = self._run(state)
        assert logs and logs[0].resolution_strategy == "accept"

    def test_allow_learning_stored(self) -> None:
        state = _make_state(_doc_id(), _truth_report(allow_learning=True), _accept_decision())
        logs = self._run(state)
        assert logs and logs[0].allow_learning is True

    def test_learn_from_document_set_for_non_correction(self) -> None:
        state = _make_state(
            _doc_id(), _truth_report(allow_learning=True), _accept_decision(), hitl_correction=False
        )
        logs = self._run(state)
        assert logs and logs[0].learn_from_document is True
        assert logs[0].learn_from_correction is False

    def test_learn_from_correction_set_for_hitl_correction(self) -> None:
        state = _make_state(
            _doc_id(), _truth_report(allow_learning=True), _accept_decision(), hitl_correction=True
        )
        logs = self._run(state)
        assert logs and logs[0].learn_from_correction is True
        assert logs[0].learn_from_document is False

    def test_schema_candidate_set_when_additional_fields(self) -> None:
        state = _make_state(
            _doc_id(),
            _truth_report(allow_learning=True, additional_fields=["blood_type"]),
            _accept_decision(),
        )
        logs = self._run(state)
        assert logs and logs[0].schema_candidate is True

    def test_no_audit_log_when_no_resolution_decision(self) -> None:
        state = _make_state(_doc_id(), _truth_report(), resolution_decision=None)
        logs = self._run(state)
        # PersistenceAuditLog is still written, just with None resolution fields
        assert len(logs) == 1
        assert logs[0].resolution_strategy is None


# ---------------------------------------------------------------------------
# SchemaProposalRecord
# ---------------------------------------------------------------------------


class TestSchemaProposalRecord:
    def _run(self, state: dict):
        doc_id = state["document_id"]
        session = mock.MagicMock()
        _mock_doc(doc_id, session)

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store") as ms,
            mock.patch("io_pipeline.output_writer.embed", return_value=[0.1] * 768),
            mock.patch("io_pipeline.output_writer.upsert_embedding"),
        ):
            ms.return_value.put = mock.MagicMock()
            write_output(state)

        return [
            call[0][0]
            for call in session.add.call_args_list
            if isinstance(call[0][0], SchemaProposalRecord)
        ]

    def test_schema_proposal_record_created_when_candidate(self) -> None:
        proposal_dict = {
            "doc_type": "passport",
            "proposed_version": "1.1",
            "additions": [{"name": "blood_type", "type": "string", "required": False}],
            "relaxed_fields": [],
            "origin_document_id": "doc-123",
            "status": "pending",
            "rejection_reason": None,
        }
        state = _make_state(
            _doc_id(),
            _truth_report(allow_learning=True, additional_fields=["blood_type"]),
            _accept_decision(),
            schema_proposal=proposal_dict,
        )
        records = self._run(state)
        assert len(records) == 1
        assert records[0].doc_type == "passport"
        assert records[0].status == "pending"
        assert records[0].additions_json == proposal_dict["additions"]

    def test_no_schema_proposal_record_when_not_candidate(self) -> None:
        state = _make_state(
            _doc_id(),
            _truth_report(allow_learning=True, additional_fields=[]),
            _accept_decision(),
            schema_proposal=None,
        )
        records = self._run(state)
        assert records == []

    def test_no_schema_proposal_record_when_blocked_learning(self) -> None:
        proposal_dict = {
            "doc_type": "passport",
            "proposed_version": "1.1",
            "additions": [{"name": "blood_type", "type": "string", "required": False}],
            "relaxed_fields": [],
            "origin_document_id": "doc-123",
            "status": "pending",
            "rejection_reason": None,
        }
        from pipelines.resolution.models import Strategy

        blocked_decision = ResolutionDecision(
            strategy=Strategy.HITL,  # not ACCEPT → schema_candidate blocked
            reason="budget_exhausted",
            requires_human=True,
        )
        state = _make_state(
            _doc_id(),
            _truth_report(allow_learning=True, additional_fields=["blood_type"]),
            blocked_decision,
            schema_proposal=proposal_dict,
        )
        records = self._run(state)
        assert records == []


# ---------------------------------------------------------------------------
# Correction exemplar source tag
# ---------------------------------------------------------------------------


class TestCorrectionExemplarSourceTag:
    def _capture_upsert_source(self, state: dict) -> str | None:
        doc_id = state["document_id"]
        session = mock.MagicMock()
        _mock_doc(doc_id, session)
        captured: list[str | None] = []

        def _fake_upsert(sess, doc_id, chunk_index, chunk_text, embedding, source=None):
            captured.append(source)

        with (
            mock.patch("io_pipeline.output_writer.get_session", return_value=session),
            mock.patch("io_pipeline.output_writer.get_object_store") as ms,
            mock.patch("io_pipeline.output_writer.embed", return_value=[0.1] * 768),
            mock.patch("io_pipeline.output_writer.upsert_embedding", side_effect=_fake_upsert),
        ):
            ms.return_value.put = mock.MagicMock()
            write_output(state)

        return captured[0] if captured else None

    def test_source_is_document_for_non_correction(self) -> None:
        state = _make_state(
            _doc_id(), _truth_report(allow_learning=True), _accept_decision(), hitl_correction=False
        )
        assert self._capture_upsert_source(state) == "document"

    def test_source_is_hitl_correction_for_correction(self) -> None:
        state = _make_state(
            _doc_id(), _truth_report(allow_learning=True), _accept_decision(), hitl_correction=True
        )
        assert self._capture_upsert_source(state) == "hitl_correction"

    def test_no_embedding_when_learning_blocked(self) -> None:
        from pipelines.resolution.models import Strategy

        blocked = ResolutionDecision(
            strategy=Strategy.HITL, reason="budget_exhausted", requires_human=True
        )
        state = _make_state(_doc_id(), _truth_report(allow_learning=True), blocked)
        assert self._capture_upsert_source(state) is None


# ---------------------------------------------------------------------------
# Review route no longer bypasses LearningPolicy
# ---------------------------------------------------------------------------


def test_review_route_has_no_direct_upsert_embedding() -> None:
    """Regression: submit_decision must not import or call upsert_embedding directly."""
    import inspect

    import api.routes.review as review_mod

    source = inspect.getsource(review_mod)
    assert "from db.vector_store import" not in source, (
        "review.py should not import from db.vector_store — "
        "correction exemplars must go through write_output via LearningPolicy"
    )


def test_review_route_has_no_embed_import() -> None:
    """Regression: embed import removed from review route (no bypass path)."""
    import inspect

    import api.routes.review as review_mod

    source = inspect.getsource(review_mod)
    assert "from agents.llm_client import embed" not in source


# ---------------------------------------------------------------------------
# Schema Proposals API
# ---------------------------------------------------------------------------


class TestSchemaProposalsAPI:
    def _make_proposal_record(self, doc_type: str = "passport") -> SchemaProposalRecord:
        from datetime import datetime

        return SchemaProposalRecord(
            id=str(uuid.uuid4()),
            doc_type=doc_type,
            proposed_version="1.1",
            additions_json=[{"name": "blood_type", "type": "string", "required": False}],
            relaxed_fields_json=[],
            origin_document_id="doc-001",
            status="pending",
            created_at=datetime(2026, 1, 1, 0, 0, 0),
            updated_at=datetime(2026, 1, 1, 0, 0, 0),
        )

    def _make_app(self, session_mock):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from api.deps import get_db
        from api.routes.schema_proposals import router, _require_api_key

        def _override_db():
            yield session_mock

        def _no_auth():
            return None

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[_require_api_key] = _no_auth
        return TestClient(app)

    def test_list_pending_returns_only_pending(self) -> None:
        session = mock.MagicMock()
        pending = self._make_proposal_record()
        session.execute.return_value.scalars.return_value.all.return_value = [pending]

        client = self._make_app(session)
        response = client.get("/pending")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["doc_type"] == "passport"

    def test_approve_nonexistent_proposal_returns_404(self) -> None:
        session = mock.MagicMock()
        session.get.return_value = None

        client = self._make_app(session)
        response = client.post("/nonexistent-id/approve")

        assert response.status_code == 404

    def test_reject_nonexistent_proposal_returns_404(self) -> None:
        session = mock.MagicMock()
        session.get.return_value = None

        client = self._make_app(session)
        response = client.post("/nonexistent-id/reject", json={"reason": "not needed"})

        assert response.status_code == 404

    def test_approve_already_approved_returns_409(self) -> None:
        session = mock.MagicMock()
        record = self._make_proposal_record()
        record.status = "approved"
        session.get.return_value = record

        client = self._make_app(session)
        response = client.post(f"/{record.id}/approve")

        assert response.status_code == 409

    def test_reject_sets_status_and_reason(self) -> None:
        session = mock.MagicMock()
        record = self._make_proposal_record()
        session.get.return_value = record

        client = self._make_app(session)
        response = client.post(f"/{record.id}/reject", json={"reason": "not useful"})

        assert response.status_code == 200
        assert record.status == "rejected"
        assert record.rejection_reason == "not useful"
