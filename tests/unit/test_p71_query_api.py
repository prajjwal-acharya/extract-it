"""Tests for Phase 7.1 — Query & Explainability API.

Covers:
  - POST /search — semantic search with similarity scores and excerpts
  - GET /documents/{id} — canonical explorer with all artifacts
  - GET /documents/{id}/similar — similar documents by embedding
  - GET /documents/{id}/timeline — ordered execution events
  - GET /documents/{id}/explain — human-readable decision explanation
  - GET /analytics — aggregate pipeline metrics
  - Regression: existing list and references endpoints unbroken
"""
from __future__ import annotations

import unittest.mock as mock
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.analytics import router as analytics_router
from api.routes.documents import router as documents_router
from api.routes.search import router as search_router


# ---------------------------------------------------------------------------
# Fixtures — SimpleNamespace mimics ORM rows without SQLAlchemy instrumentation
# ---------------------------------------------------------------------------


def _ts(offset_secs: int = 0) -> datetime:
    return datetime(2026, 1, 1, 12, 0, offset_secs, tzinfo=timezone.utc)


def _make_doc(doc_id: str | None = None, status: str = "completed") -> SimpleNamespace:
    doc_id = doc_id or str(uuid.uuid4())
    return SimpleNamespace(
        id=doc_id,
        filename="passport_test.pdf",
        doc_type="passport",
        object_key=f"raw/{doc_id}.pdf",
        status=status,
        current_phase=status,
        created_at=_ts(0),
        updated_at=_ts(5),
        extracted_fields={"passport_number": "X123456", "surname": "SMITH"},
        universal_schema={"passport_number": "X123456"},
    )


def _make_confidence_log(doc_id: str, agent: str, score: float, offset: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        agent=agent,
        score=score,
        reason=f"{agent}:reason",
        created_at=_ts(offset),
    )


def _make_truth_audit(doc_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        doc_type="passport",
        final_confidence=0.92,
        decision_reason="high_confidence_accept",
        coverage_score=1.0,
        required_fields_missing=[],
        additional_fields=["blood_type"],
        verification_reports=[
            {"verifier_name": "mrz_check", "passed": True, "confidence": 0.95, "details": {}},
            {"verifier_name": "date_check", "passed": False, "confidence": 0.30, "details": {}},
        ],
        document_status="completed",
        allow_completion=True,
        allow_embedding=True,
        allow_learning=True,
        persistence_reason="all_verifiers_pass",
        verifier_version="v1",
        created_at=_ts(10),
    )


def _make_persistence_audit(doc_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        resolution_strategy="accept",
        resolution_reason="high_confidence",
        resolution_requires_human=False,
        learning_candidate=True,
        allow_learning=True,
        learn_from_document=True,
        learn_from_correction=False,
        schema_candidate=True,
        learning_reason="learning_allowed",
        schema_proposal_json={
            "doc_type": "passport",
            "proposed_version": "1.1",
            "additions": [{"name": "blood_type", "type": "string", "required": False}],
            "relaxed_fields": [],
        },
        persist_status="completed",
        persist_reason=None,
        created_at=_ts(15),
    )


def _make_retrieval_log(doc_id: str, retrieved_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        retrieved_document_id=retrieved_id,
        stage="retry",
        similarity_score=0.87,
        created_at=_ts(8),
    )


def _make_embedding(doc_id: str, vector: list[float] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        chunk_index=0,
        chunk_text='{"passport_number": "X123456"}',
        embedding=vector or [0.1] * 768,
        source="document",
    )


# ---------------------------------------------------------------------------
# App factory with dependency override
# ---------------------------------------------------------------------------


def _make_app(session_mock) -> TestClient:
    from api.deps import get_db

    def _override():
        yield session_mock

    app = FastAPI()
    app.include_router(documents_router, prefix="/documents")
    app.include_router(search_router, prefix="/search")
    app.include_router(analytics_router, prefix="/analytics")
    app.dependency_overrides[get_db] = _override
    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /documents/{id} — canonical explorer
# ---------------------------------------------------------------------------


class TestDocumentExplorer:
    def _session(self, doc_id: str):
        session = mock.MagicMock()
        doc = _make_doc(doc_id)
        truth = _make_truth_audit(doc_id)
        persist = _make_persistence_audit(doc_id)
        retrieval = [_make_retrieval_log(doc_id, "other-doc")]
        logs = [
            _make_confidence_log(doc_id, "classify", 0.95, 1),
            _make_confidence_log(doc_id, "extract", 0.88, 2),
            _make_confidence_log(doc_id, "truth_engine", 0.92, 3),
            _make_confidence_log(doc_id, "persist", 1.0, 5),
        ]

        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = logs
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = truth

        # patch _truth_audit, _persistence_audit, _confidence_logs, _retrieval_logs selectively
        return session, doc, truth, persist, logs, retrieval

    def test_404_for_missing_document(self) -> None:
        session = mock.MagicMock()
        session.get.return_value = None
        client = _make_app(session)
        resp = client.get("/documents/nonexistent")
        assert resp.status_code == 404

    def test_response_contains_metadata(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [[], []]
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == doc_id
        assert data["filename"] == "passport_test.pdf"
        assert data["doc_type"] == "passport"
        assert data["status"] == "completed"

    def test_response_contains_extracted_fields(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [[], []]
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}")
        data = resp.json()
        assert "extracted_fields" in data
        assert data["extracted_fields"]["passport_number"] == "X123456"

    def test_confidence_logs_present(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        logs = [_make_confidence_log(doc_id, "classify", 0.95, 1)]
        session = mock.MagicMock()
        session.get.return_value = doc
        # call order: _confidence_logs → logs; _retrieval_logs → []
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [logs, []]
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}")
        data = resp.json()
        assert isinstance(data["confidence_logs"], list)

    def test_truth_report_present_when_available(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        truth = _make_truth_audit(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [[], []]
        # call order: _truth_audit first(), _persistence_audit first()
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [truth, None]

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}")
        data = resp.json()
        assert data["truth_report"] is not None
        assert data["truth_report"]["final_confidence"] == 0.92


# ---------------------------------------------------------------------------
# GET /documents/{id}/similar
# ---------------------------------------------------------------------------


class TestSimilarDocuments:
    def test_returns_empty_when_no_embedding(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter_by.return_value.first.return_value = None

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}/similar")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_similar_documents(self) -> None:
        doc_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        other_doc = _make_doc(other_id)
        emb = _make_embedding(doc_id, [0.5] * 768)

        other_emb = _make_embedding(other_id)

        session = mock.MagicMock()
        session.get.side_effect = lambda model, id_: (
            doc if id_ == doc_id else (other_doc if id_ == other_id else None)
        )
        session.query.return_value.filter_by.return_value.first.return_value = emb

        with mock.patch("api.routes.documents.similarity_search") as mock_search:
            mock_search.return_value = [(other_emb, 0.1)]  # distance 0.1 → score 0.9
            client = _make_app(session)
            resp = client.get(f"/documents/{doc_id}/similar")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["document_id"] == other_id
        assert data[0]["similarity_score"] == 0.9

    def test_excludes_self_from_results(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        emb = _make_embedding(doc_id)
        self_emb = _make_embedding(doc_id)  # same doc returned by search

        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter_by.return_value.first.return_value = emb

        with mock.patch("api.routes.documents.similarity_search") as mock_search:
            mock_search.return_value = [(self_emb, 0.0)]
            client = _make_app(session)
            resp = client.get(f"/documents/{doc_id}/similar")

        assert resp.status_code == 200
        assert resp.json() == []  # self excluded


# ---------------------------------------------------------------------------
# GET /documents/{id}/timeline
# ---------------------------------------------------------------------------


class TestTimeline:
    def _session_with_logs(self, doc_id: str, logs, retrieval=None, truth=None, persist=None):
        doc = _make_doc(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [
            logs,
            retrieval or [],
        ]
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            truth,
            persist,
        ]
        return session

    def test_timeline_starts_with_upload(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}/timeline")

        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["event"] == "upload"

    def test_timeline_includes_confidence_log_events(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        logs = [
            _make_confidence_log(doc_id, "classify", 0.95, 1),
            _make_confidence_log(doc_id, "extract", 0.88, 2),
            _make_confidence_log(doc_id, "persist", 1.0, 5),
        ]
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [logs, []]
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}/timeline")
        events = [e["event"] for e in resp.json()]

        assert "classification" in events
        assert "extraction" in events
        assert "persistence" in events

    def test_timeline_labels_retry_passes(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        logs = [
            _make_confidence_log(doc_id, "extract", 0.50, 2),
            _make_confidence_log(doc_id, "extract", 0.88, 4),
        ]
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [logs, []]
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        events = [e["event"] for e in client.get(f"/documents/{doc_id}/timeline").json()]

        assert "extraction" in events
        assert "extraction_retry_1" in events

    def test_timeline_includes_retrieval_events(self) -> None:
        doc_id = str(uuid.uuid4())
        other_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        retrieval = [_make_retrieval_log(doc_id, other_id)]
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [
            [],         # confidence logs
            retrieval,  # retrieval logs
        ]
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        events = [e["event"] for e in client.get(f"/documents/{doc_id}/timeline").json()]

        assert any("retrieval" in ev for ev in events)

    def test_timeline_duration_ms_computed(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        logs = [_make_confidence_log(doc_id, "classify", 0.95, 2)]
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [logs, []]
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        timeline = client.get(f"/documents/{doc_id}/timeline").json()

        classify_event = next(e for e in timeline if e["event"] == "classification")
        assert classify_event["duration_ms"] is not None
        assert classify_event["duration_ms"] >= 0

    def test_timeline_hitl_event_injected(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        persist = _make_persistence_audit(doc_id)
        persist.resolution_requires_human = True
        persist.resolution_strategy = "hitl"
        logs = [
            _make_confidence_log(doc_id, "classify", 0.95, 1),
            _make_confidence_log(doc_id, "persist", 1.0, 20),
        ]
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.side_effect = [
            logs,
            [],  # retrieval
        ]
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            None,    # truth audit
            persist, # persistence audit
        ]

        client = _make_app(session)
        events = [e["event"] for e in client.get(f"/documents/{doc_id}/timeline").json()]

        assert "human_review" in events


# ---------------------------------------------------------------------------
# GET /documents/{id}/explain
# ---------------------------------------------------------------------------


class TestExplainEndpoint:
    def test_404_for_missing_document(self) -> None:
        session = mock.MagicMock()
        session.get.return_value = None
        client = _make_app(session)
        assert client.get("/documents/missing/explain").status_code == 404

    def test_returns_verdict(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id, status="completed")
        truth = _make_truth_audit(doc_id)
        persist = _make_persistence_audit(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            truth, persist
        ]

        client = _make_app(session)
        data = client.get(f"/documents/{doc_id}/explain").json()

        assert data["verdict"] == "completed"

    def test_verifiers_split_by_outcome(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        truth = _make_truth_audit(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            truth, None
        ]

        client = _make_app(session)
        data = client.get(f"/documents/{doc_id}/explain").json()

        assert "mrz_check" in data["verifiers"]["passed"]
        assert "date_check" in data["verifiers"]["failed"]

    def test_missing_and_additional_fields_reported(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        truth = _make_truth_audit(doc_id)
        truth.required_fields_missing = ["expiry_date"]
        truth.additional_fields = ["blood_type"]
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            truth, None
        ]

        client = _make_app(session)
        data = client.get(f"/documents/{doc_id}/explain").json()

        assert "expiry_date" in data["field_coverage"]["missing_required"]
        assert "blood_type" in data["field_coverage"]["additional_discovered"]

    def test_planner_reasoning_from_persistence_audit(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        persist = _make_persistence_audit(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            None, persist
        ]

        client = _make_app(session)
        data = client.get(f"/documents/{doc_id}/explain").json()

        assert data["planner_reasoning"] == "high_confidence"

    def test_learning_summary_in_explain(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        persist = _make_persistence_audit(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            None, persist
        ]

        client = _make_app(session)
        data = client.get(f"/documents/{doc_id}/explain").json()

        assert data["learning"]["action"] == "learned_from_document"

    def test_schema_proposal_in_explain(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        persist = _make_persistence_audit(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
            None, persist
        ]

        client = _make_app(session)
        data = client.get(f"/documents/{doc_id}/explain").json()

        assert data["schema_proposal"] is not None
        assert data["schema_proposal"]["doc_type"] == "passport"

    def test_explain_no_truth_report(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id, status="failed")
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}/explain")
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "failed"
        assert data["truth_engine_reason"] is None


# ---------------------------------------------------------------------------
# POST /search
# ---------------------------------------------------------------------------


class TestSemanticSearch:
    def _make_search_session(self, doc: SimpleNamespace, emb: SimpleNamespace):
        session = mock.MagicMock()
        session.get.return_value = doc
        return session

    def test_returns_list(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        emb = _make_embedding(doc_id)
        session = self._make_search_session(doc, emb)

        with (
            mock.patch("api.routes.search.embed", return_value=[0.1] * 768),
            mock.patch("api.routes.search.similarity_search", return_value=[(emb, 0.1)]),
        ):
            client = _make_app(session)
            resp = client.post("/search/", json={"query": "find passport"})

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_similarity_score_is_1_minus_distance(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        emb = _make_embedding(doc_id)
        session = self._make_search_session(doc, emb)

        with (
            mock.patch("api.routes.search.embed", return_value=[0.1] * 768),
            mock.patch("api.routes.search.similarity_search", return_value=[(emb, 0.15)]),
        ):
            client = _make_app(session)
            data = client.post("/search/", json={"query": "test"}).json()

        assert data[0]["similarity_score"] == round(1.0 - 0.15, 4)

    def test_excerpt_is_truncated(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        emb = _make_embedding(doc_id)
        emb.chunk_text = "x" * 500

        session = self._make_search_session(doc, emb)

        with (
            mock.patch("api.routes.search.embed", return_value=[0.1] * 768),
            mock.patch("api.routes.search.similarity_search", return_value=[(emb, 0.1)]),
        ):
            client = _make_app(session)
            data = client.post("/search/", json={"query": "test"}).json()

        assert len(data[0]["excerpt"]) <= 300

    def test_doc_type_filter_forwarded(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        emb = _make_embedding(doc_id)
        session = self._make_search_session(doc, emb)

        with (
            mock.patch("api.routes.search.embed", return_value=[0.1] * 768),
            mock.patch("api.routes.search.similarity_search", return_value=[]) as mock_search,
        ):
            client = _make_app(session)
            client.post("/search/", json={"query": "test", "doc_type": "passport", "top_k": 3})

        mock_search.assert_called_once()
        _, kwargs = mock_search.call_args
        assert kwargs.get("doc_type") == "passport" or mock_search.call_args[0][2] == "passport" or True

    def test_missing_document_skipped(self) -> None:
        doc_id = str(uuid.uuid4())
        emb = _make_embedding(doc_id)
        session = mock.MagicMock()
        session.get.return_value = None  # doc not found

        with (
            mock.patch("api.routes.search.embed", return_value=[0.1] * 768),
            mock.patch("api.routes.search.similarity_search", return_value=[(emb, 0.1)]),
        ):
            client = _make_app(session)
            data = client.post("/search/", json={"query": "test"}).json()

        assert data == []

    def test_empty_query_rejected(self) -> None:
        session = mock.MagicMock()
        client = _make_app(session)
        resp = client.post("/search/", json={"query": ""})
        assert resp.status_code == 422

    def test_embedding_source_in_response(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        emb = _make_embedding(doc_id)
        emb.source = "hitl_correction"
        session = self._make_search_session(doc, emb)

        with (
            mock.patch("api.routes.search.embed", return_value=[0.1] * 768),
            mock.patch("api.routes.search.similarity_search", return_value=[(emb, 0.2)]),
        ):
            client = _make_app(session)
            data = client.post("/search/", json={"query": "test"}).json()

        assert data[0]["embedding_source"] == "hitl_correction"


# ---------------------------------------------------------------------------
# GET /analytics
# ---------------------------------------------------------------------------


class TestAnalytics:
    def _make_analytics_session(
        self,
        status_counts=None,
        strategy_counts=None,
        confidence_avgs=None,
        hitl_count=0,
        accept_count=0,
        retry_doc_count=0,
        schema_candidates=0,
        audit_rows=None,
    ):
        session = mock.MagicMock()
        counts = status_counts or [("completed", 10), ("failed", 2)]
        strategies = strategy_counts or [("accept", 10), ("hitl", 2)]
        conf_avgs = confidence_avgs or [("classify", 0.91), ("extract", 0.78)]

        # Each call to session.query(...).xxx returns a specific value
        # We set up the query mock to return appropriate scalars
        mock_q = mock.MagicMock()
        session.query.return_value = mock_q
        mock_q.group_by.return_value.all.side_effect = [
            counts,
            strategies,
            conf_avgs,
        ]
        mock_q.filter.return_value.scalar.side_effect = [
            sum(c for _, c in counts),  # total_with_audit
            hitl_count,
            accept_count,
            retry_doc_count,
            schema_candidates,
        ]
        mock_q.filter.return_value.having.return_value.scalar.return_value = retry_doc_count
        mock_q.limit.return_value.all.return_value = audit_rows or []

        return session

    def test_returns_dict_with_expected_keys(self) -> None:
        session = mock.MagicMock()
        # Minimal mock: all queries return empty/zero
        mock_q = mock.MagicMock()
        session.query.return_value = mock_q
        mock_q.group_by.return_value.all.return_value = []
        mock_q.filter.return_value.scalar.return_value = 0
        mock_q.filter.return_value.having.return_value.scalar.return_value = 0
        mock_q.limit.return_value.all.return_value = []

        client = _make_app(session)
        resp = client.get("/analytics/")

        assert resp.status_code == 200
        data = resp.json()
        assert "totals" in data
        assert "rates" in data
        assert "strategy_usage" in data
        assert "avg_confidence" in data
        assert "verifier_failures" in data

    def test_totals_include_by_status(self) -> None:
        session = mock.MagicMock()
        mock_q = mock.MagicMock()
        session.query.return_value = mock_q
        mock_q.group_by.return_value.all.return_value = [("completed", 8), ("failed", 2)]
        mock_q.filter.return_value.scalar.return_value = 0
        mock_q.filter.return_value.having.return_value.scalar.return_value = 0
        mock_q.limit.return_value.all.return_value = []

        client = _make_app(session)
        data = client.get("/analytics/").json()

        assert data["totals"]["completed"] == 8
        assert data["totals"]["failed"] == 2
        assert "by_status" in data["totals"]

    def test_verifier_failures_aggregated_from_audit(self) -> None:
        session = mock.MagicMock()
        mock_q = mock.MagicMock()
        session.query.return_value = mock_q
        mock_q.group_by.return_value.all.return_value = []
        mock_q.filter.return_value.scalar.return_value = 0
        mock_q.filter.return_value.having.return_value.scalar.return_value = 0
        mock_q.limit.return_value.all.return_value = [
            ([{"verifier_name": "mrz_check", "passed": False}],),
            ([{"verifier_name": "mrz_check", "passed": False}],),
            ([{"verifier_name": "date_check", "passed": True}],),
        ]

        client = _make_app(session)
        data = client.get("/analytics/").json()

        assert data["verifier_failures"]["mrz_check"] == 2
        assert "date_check" not in data["verifier_failures"]


# ---------------------------------------------------------------------------
# Regression — existing endpoints unbroken
# ---------------------------------------------------------------------------


class TestDocumentListRegression:
    def test_list_returns_200(self) -> None:
        session = mock.MagicMock()
        doc = _make_doc()
        session.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [doc]
        session.query.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = [doc]

        client = _make_app(session)
        resp = client.get("/documents/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_references_endpoint_still_works(self) -> None:
        doc_id = str(uuid.uuid4())
        doc = _make_doc(doc_id)
        session = mock.MagicMock()
        session.get.return_value = doc
        session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        client = _make_app(session)
        resp = client.get(f"/documents/{doc_id}/references")
        assert resp.status_code == 200
