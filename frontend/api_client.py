"""Thin HTTP wrapper around the Doc Intel Platform API.

All dashboard pages import this module — no direct requests calls in page code.
Base URL and API key come from environment variables so the dashboard works
both locally (against localhost:8000) and in Docker (against app:8000).
"""

from __future__ import annotations

import os
from typing import Any

import requests

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")
_API_KEY = os.getenv("REVIEW_API_KEY", "")
_TIMEOUT = 20


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    def __init__(self, base_url: str = API_BASE, api_key: str = _API_KEY) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key

    def _hdrs(self) -> dict[str, str]:
        return {"X-API-Key": self._key} if self._key else {}

    def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            r = requests.get(
                f"{self._base}{path}", params=params, headers=self._hdrs(), timeout=_TIMEOUT
            )
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            raise ApiError(
                str(e), e.response.status_code if e.response is not None else None
            ) from e
        except requests.RequestException as e:
            raise ApiError(str(e)) from e

    def _post(self, path: str, json: Any = None, files: Any = None) -> Any:
        try:
            r = requests.post(
                f"{self._base}{path}",
                json=json,
                files=files,
                headers=self._hdrs() if files is None else self._hdrs(),
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            raise ApiError(
                str(e), e.response.status_code if e.response is not None else None
            ) from e
        except requests.RequestException as e:
            raise ApiError(str(e)) from e

    # ── Ingest ────────────────────────────────────────────────────────────────

    def ingest(self, file_bytes: bytes, filename: str) -> dict:
        return self._post("/ingest/", files={"file": (filename, file_bytes)})

    # ── Documents ─────────────────────────────────────────────────────────────

    def list_documents(
        self,
        status: str | None = None,
        doc_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        params: dict = {"limit": limit, "offset": offset}
        if status:
            params["status"] = status
        if doc_type:
            params["doc_type"] = doc_type
        return self._get("/documents/", params=params)

    def get_document(self, doc_id: str) -> dict:
        return self._get(f"/documents/{doc_id}")

    def get_similar(self, doc_id: str, top_k: int = 5) -> list[dict]:
        return self._get(f"/documents/{doc_id}/similar", params={"top_k": top_k})

    def get_timeline(self, doc_id: str) -> list[dict]:
        return self._get(f"/documents/{doc_id}/timeline")

    def get_explain(self, doc_id: str) -> dict:
        return self._get(f"/documents/{doc_id}/explain")

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, doc_type: str | None = None, top_k: int = 5) -> list[dict]:
        payload: dict = {"query": query, "top_k": top_k}
        if doc_type:
            payload["doc_type"] = doc_type
        return self._post("/search/", json=payload)

    # ── Analytics ─────────────────────────────────────────────────────────────

    def get_analytics(self) -> dict:
        return self._get("/analytics/")

    # ── Review ────────────────────────────────────────────────────────────────

    def get_pending_review(self) -> list[dict]:
        return self._get("/review/pending")

    def submit_review(self, doc_id: str, approved: bool, corrections: dict | None = None) -> dict:
        return self._post(
            f"/review/{doc_id}/decision",
            json={"approved": approved, "corrections": corrections or {}},
        )

    # ── Schema Proposals ──────────────────────────────────────────────────────

    def get_pending_proposals(self) -> list[dict]:
        return self._get("/schema-proposals/pending")

    def approve_proposal(self, proposal_id: str) -> dict:
        return self._post(f"/schema-proposals/{proposal_id}/approve")

    def reject_proposal(self, proposal_id: str, reason: str) -> dict:
        return self._post(f"/schema-proposals/{proposal_id}/reject", json={"reason": reason})

    # ── Knowledge Graph ───────────────────────────────────────────────────────

    def get_knowledge_graph(self, limit: int = 50) -> dict:
        return self._get("/knowledge-graph/", params={"limit": limit})

    # ── LLM Query ─────────────────────────────────────────────────────────────

    def query(self, question: str) -> dict:
        return self._post("/query/", json={"question": question})


# Module-level singleton — pages import this directly
client = ApiClient()
