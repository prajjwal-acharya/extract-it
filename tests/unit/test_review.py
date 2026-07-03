import unittest.mock as mock

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app


@pytest.fixture
def client(postgres_session):
    app.dependency_overrides[get_db] = lambda: postgres_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _mock_graph(state_values=None, invoke_result=None):
    """Return a mock graph with get_state and invoke pre-configured."""
    g = mock.MagicMock()
    snapshot = mock.MagicMock()
    snapshot.values = state_values if state_values is not None else {"doc_type": "passport", "hitl_required": True}
    g.get_state.return_value = snapshot
    g.invoke.return_value = invoke_result or {}
    return g


# get_graph is a deferred import inside submit_decision — patch at the source module
_GRAPH_PATCH = "pipelines.graph.get_graph"


def test_decision_rejects_unknown_correction_fields(client) -> None:
    """422 when corrections contain fields not in the doc_type schema."""
    with mock.patch(_GRAPH_PATCH, return_value=_mock_graph()):
        resp = client.post(
            "/review/some-doc-id/decision",
            json={"approved": True, "corrections": {"nonexistent_field": "value"}},
        )
    assert resp.status_code == 422


def test_decision_returns_404_for_unknown_thread_id(client) -> None:
    """404 when no checkpoint exists for the given document_id."""
    g = _mock_graph(state_values={})  # empty values → no pending review
    with mock.patch(_GRAPH_PATCH, return_value=g):
        resp = client.post(
            "/review/unknown-thread-id/decision",
            json={"approved": True},
        )
    assert resp.status_code == 404


def test_decision_requires_api_key_when_configured(client, monkeypatch) -> None:
    """401 when REVIEW_API_KEY is set and the request omits X-API-Key."""
    monkeypatch.setattr("api.routes.review.settings.REVIEW_API_KEY", "secret-key")

    with mock.patch(_GRAPH_PATCH, return_value=_mock_graph()):
        resp = client.post(
            "/review/some-doc-id/decision",
            json={"approved": True},
            # No X-API-Key header
        )
    assert resp.status_code == 401


def test_decision_passes_with_correct_api_key(client, monkeypatch) -> None:
    """Request succeeds when the correct X-API-Key is provided."""
    monkeypatch.setattr("api.routes.review.settings.REVIEW_API_KEY", "secret-key")

    with mock.patch(_GRAPH_PATCH, return_value=_mock_graph()):
        resp = client.post(
            "/review/some-doc-id/decision",
            json={"approved": True},
            headers={"X-API-Key": "secret-key"},
        )
    assert resp.status_code == 200


def test_decision_allows_valid_correction_fields(client) -> None:
    """Valid passport field in corrections should not raise 422."""
    with mock.patch(_GRAPH_PATCH, return_value=_mock_graph()):
        resp = client.post(
            "/review/some-doc-id/decision",
            json={"approved": True, "corrections": {"surname": "CORRECTED"}},
        )
    assert resp.status_code == 200
