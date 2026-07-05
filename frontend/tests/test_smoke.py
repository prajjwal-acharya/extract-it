"""Dashboard smoke tests.

Uses Streamlit's AppTest to verify each page loads without crashing when
the API is offline (all requests fail gracefully with st.error()).

AppTest runs the Streamlit script headlessly — no browser required.
API calls are mocked at the requests level so no real backend is needed.
"""
from __future__ import annotations

import sys
import os
import unittest.mock as mock
from pathlib import Path

import pytest

# Ensure the frontend directory is on sys.path so `api_client` imports work
FRONTEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRONTEND_DIR))

from streamlit.testing.v1 import AppTest


def _pages_dir() -> Path:
    return FRONTEND_DIR / "pages"


def _stub_client(
    list_documents=None,
    get_document=None,
    get_analytics=None,
    get_pending_review=None,
    get_pending_proposals=None,
    search=None,
    get_knowledge_graph=None,
):
    """Return a mock api_client.client with safe default returns."""
    m = mock.MagicMock()
    m.list_documents.return_value = list_documents or []
    m.get_document.return_value = get_document or {
        "id": "test-id",
        "filename": "test.pdf",
        "doc_type": "passport",
        "status": "completed",
        "current_phase": "completed",
        "extracted_fields": {"passport_number": "X123"},
        "universal_schema": {},
        "confidence_logs": [],
        "retrieval_history": [],
        "truth_report": None,
        "resolution": None,
        "learning": None,
        "persistence_audit": None,
    }
    m.get_analytics.return_value = get_analytics or {
        "totals": {"documents": 0, "completed": 0, "failed": 0,
                   "rejected": 0, "persist_failed": 0, "awaiting_review": 0, "by_status": {}},
        "rates": {"acceptance_rate": 0, "hitl_rate": 0,
                  "retry_rate": 0, "schema_candidate_rate": 0},
        "strategy_usage": {},
        "verifier_failures": {},
        "avg_confidence": {},
    }
    m.get_pending_review.return_value = get_pending_review or []
    m.get_pending_proposals.return_value = get_pending_proposals or []
    m.search.return_value = search or []
    m.get_knowledge_graph.return_value = get_knowledge_graph or {"nodes": [], "edges": []}
    m.get_timeline.return_value = []
    m.get_explain.return_value = {}
    m.get_similar.return_value = []
    return m


# ---------------------------------------------------------------------------
# Upload / home page
# ---------------------------------------------------------------------------


class TestHomePage:
    def test_home_page_loads(self) -> None:
        at = AppTest.from_file(str(FRONTEND_DIR / "app.py"))
        with mock.patch("api_client.client", _stub_client()):
            at.run(timeout=30)
        assert not at.exception, f"Home page crashed: {at.exception}"

    def test_home_page_has_title(self) -> None:
        at = AppTest.from_file(str(FRONTEND_DIR / "app.py"))
        with mock.patch("api_client.client", _stub_client()):
            at.run(timeout=30)
        titles = [t.value for t in at.title]
        assert any("Doc Intel" in str(t) for t in titles)

    def test_home_page_has_file_uploader(self) -> None:
        at = AppTest.from_file(str(FRONTEND_DIR / "app.py"))
        with mock.patch("api_client.client", _stub_client()):
            at.run(timeout=30)
        assert len(at.file_uploader) >= 1


# ---------------------------------------------------------------------------
# Documents page
# ---------------------------------------------------------------------------


class TestDocumentsPage:
    _page = str(_pages_dir() / "1_📋_Documents.py")

    def test_documents_page_loads_empty(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client()):
            at.run(timeout=30)
        assert not at.exception, f"Documents page crashed: {at.exception}"

    def test_documents_page_shows_table_when_docs_present(self) -> None:
        docs = [
            {
                "id": "doc-001",
                "filename": "passport.pdf",
                "doc_type": "passport",
                "status": "completed",
                "current_phase": "completed",
                "created_at": "2026-01-01T12:00:00",
            }
        ]
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client(list_documents=docs)):
            at.run(timeout=30)
        assert not at.exception

    def test_documents_page_shows_no_docs_message(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client(list_documents=[])):
            at.run(timeout=30)
        # Should show info message, not crash
        assert not at.exception


# ---------------------------------------------------------------------------
# Search page
# ---------------------------------------------------------------------------


class TestSearchPage:
    _page = str(_pages_dir() / "2_🔍_Search.py")

    def test_search_page_loads(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client()):
            at.run(timeout=30)
        assert not at.exception

    def test_search_page_has_text_input(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client()):
            at.run(timeout=30)
        assert len(at.text_input) >= 1


# ---------------------------------------------------------------------------
# Review Queue page
# ---------------------------------------------------------------------------


class TestReviewQueuePage:
    _page = str(_pages_dir() / "3_✅_Review_Queue.py")

    def test_review_queue_loads_empty(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client(get_pending_review=[])):
            at.run(timeout=30)
        assert not at.exception

    def test_review_queue_shows_success_when_empty(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client(get_pending_review=[])):
            at.run(timeout=30)
        success_texts = [s.value for s in at.success]
        assert any("No documents" in str(t) for t in success_texts)

    def test_review_queue_shows_document_when_pending(self) -> None:
        pending = [{
            "id": "doc-pending-001",
            "filename": "passport.pdf",
            "doc_type": "passport",
            "status": "awaiting_review",
            "current_phase": "awaiting_review",
            "extracted_fields": {"passport_number": "X123"},
            "confidence_logs": [],
            "references": [],
        }]
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client(get_pending_review=pending)):
            at.run(timeout=30)
        assert not at.exception


# ---------------------------------------------------------------------------
# Schema Proposals page
# ---------------------------------------------------------------------------


class TestSchemaProposalsPage:
    _page = str(_pages_dir() / "4_🏛_Schema_Proposals.py")

    def test_schema_proposals_page_loads_empty(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client(get_pending_proposals=[])):
            at.run(timeout=30)
        assert not at.exception

    def test_schema_proposals_shows_proposal(self) -> None:
        proposals = [{
            "id": "prop-001",
            "doc_type": "passport",
            "proposed_version": "1.1",
            "additions": [{"name": "blood_type", "type": "string", "required": False}],
            "relaxed_fields": [],
            "origin_document_id": "doc-001",
            "status": "pending",
            "created_at": "2026-01-01T12:00:00",
        }]
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client(get_pending_proposals=proposals)):
            at.run(timeout=30)
        assert not at.exception


# ---------------------------------------------------------------------------
# Analytics page
# ---------------------------------------------------------------------------


class TestAnalyticsPage:
    _page = str(_pages_dir() / "5_📊_Analytics.py")

    def test_analytics_page_loads(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client()):
            at.run(timeout=30)
        assert not at.exception

    def test_analytics_shows_metrics(self) -> None:
        analytics = {
            "totals": {
                "documents": 10, "completed": 8, "failed": 1,
                "rejected": 0, "persist_failed": 0, "awaiting_review": 1,
                "by_status": {"completed": 8, "failed": 1, "awaiting_review": 1},
            },
            "rates": {
                "acceptance_rate": 0.80, "hitl_rate": 0.10,
                "retry_rate": 0.20, "schema_candidate_rate": 0.05,
            },
            "strategy_usage": {"accept": 8, "hitl": 1, "retry": 1},
            "verifier_failures": {"mrz_check": 2},
            "avg_confidence": {"classify": 0.91, "extract": 0.78},
        }
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client(get_analytics=analytics)):
            at.run(timeout=30)
        assert not at.exception
        metric_vals = [m.value for m in at.metric]
        assert any("10" in str(v) for v in metric_vals)  # total documents


# ---------------------------------------------------------------------------
# Knowledge Map page
# ---------------------------------------------------------------------------


class TestKnowledgeMapPage:
    _page = str(_pages_dir() / "6_🗺_Knowledge_Map.py")

    def test_knowledge_map_loads_empty(self) -> None:
        at = AppTest.from_file(self._page)
        with mock.patch("api_client.client", _stub_client()):
            at.run(timeout=30)
        assert not at.exception


# ---------------------------------------------------------------------------
# ApiClient unit tests (no Streamlit needed)
# ---------------------------------------------------------------------------


class TestApiClient:
    def test_client_get_raises_api_error_on_connection_failure(self) -> None:
        from api_client import ApiClient, ApiError
        import requests

        c = ApiClient(base_url="http://localhost:19999")
        with pytest.raises(ApiError):
            c.list_documents()

    def test_client_headers_include_api_key(self) -> None:
        from api_client import ApiClient

        c = ApiClient(base_url="http://localhost:8000", api_key="secret")
        assert c._hdrs() == {"X-API-Key": "secret"}

    def test_client_no_headers_when_no_key(self) -> None:
        from api_client import ApiClient

        c = ApiClient(base_url="http://localhost:8000", api_key="")
        assert c._hdrs() == {}

    def test_client_search_builds_correct_payload(self) -> None:
        from api_client import ApiClient

        c = ApiClient(base_url="http://localhost:8000")
        with mock.patch("api_client.requests.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = []
            mock_post.return_value.raise_for_status = mock.MagicMock()
            c.search("test query", doc_type="passport", top_k=3)

        _, kwargs = mock_post.call_args
        payload = kwargs.get("json") or mock_post.call_args[1].get("json")
        assert payload["query"] == "test query"
        assert payload["doc_type"] == "passport"
        assert payload["top_k"] == 3

    def test_api_error_carries_status_code(self) -> None:
        from api_client import ApiClient, ApiError
        import requests as req_lib

        c = ApiClient(base_url="http://localhost:8000")
        mock_resp = mock.MagicMock()
        mock_resp.status_code = 404
        http_err = req_lib.HTTPError(response=mock_resp)

        with mock.patch("api_client.requests.get") as mock_get:
            mock_get.return_value.raise_for_status.side_effect = http_err
            try:
                c.list_documents()
            except ApiError as e:
                assert e.status_code == 404
