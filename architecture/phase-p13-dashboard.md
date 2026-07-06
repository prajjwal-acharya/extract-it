# P13 — Streamlit Dashboard

**Status:** ✅ Done  
**Scope:** 7-page multipage Streamlit UI, api_client, dark theme, smoke tests

---

## What P13 delivered

P13 is the human interface for the entire platform. It provides a 7-page Streamlit
dashboard covering document upload, browsing, semantic search, HITL review, schema
proposal approval, analytics, and the knowledge graph visualization.

---

## Architecture

```
frontend/app.py              Multipage entry point + Upload page
frontend/api_client.py       Typed HTTP client — all pages use this; no page imports requests
frontend/pages/              Page modules (Streamlit multipage convention)
frontend/.streamlit/config.toml   Dark theme
frontend/tests/test_smoke.py      Headless smoke tests
```

`API_BASE_URL` (default `http://localhost:8000`; `http://app:8000` inside Docker)
controls which API the dashboard talks to.

**Design constraints:**
- All business logic stays in the API — dashboard is a pure presentation layer.
- No page imports `requests` directly — all calls go through `api_client.client`.
- `ApiError` is caught per-page and rendered as `st.error()`, never as an unhandled exception.
- Dark theme via `frontend/.streamlit/config.toml`.

---

## Pages

### app.py — Upload

The entry point doubles as the upload page.

**What it does:**
- `st.file_uploader` for PDF, JPEG, PNG
- `POST /ingest/` on file selection
- Live status polling via `GET /documents/{id}` (manual refresh button)
- Shows current `current_phase` and `status`
- On completion: shows universal schema fields

---

### pages/0_Home.py — Home

Dashboard home page with:
- Platform overview
- Quick link to all pages
- Summary stats via `GET /analytics/`

---

### pages/1_Documents.py — Documents

Document browser.

**What it does:**
- `GET /documents/` with status/doc_type filters and pagination
- Per-document detail in tabs:
  - **Overview**: universal_schema, extracted_fields, confidence scores
  - **Timeline**: `GET /documents/{id}/timeline` as a table with duration_ms
  - **Explain**: `GET /documents/{id}/explain` — verdict, verifier pass/fail, field coverage
  - **Similar**: `GET /documents/{id}/similar` — nearest documents in embedding space

---

### pages/2_Search.py — Semantic Search

**What it does:**
- Text input for free-form query
- Optional `doc_type` filter
- `top_k` slider (1–20)
- `POST /search/` on submit
- Results table: filename, doc_type, score, excerpt
- Click a result to navigate to its Documents detail view

---

### pages/3_Review_Queue.py — Review Queue

HITL review interface.

**What it does:**
- `GET /review/pending` to list documents awaiting human review
- For each document:
  - Shows extracted fields (read from LangGraph checkpoint via API)
  - Per-field correction editor (text input for each field)
  - Approve / Reject buttons
- `POST /review/{id}/decision` with `{approved, corrections}`
- Displays result status after submission

**Note:** extracted fields come from the LangGraph checkpoint (not `doc.extracted_fields`
which is empty at interrupt time). The API handles this transparently.

---

### pages/4_Schema_Proposals.py — Schema Proposals

Human-gated schema change approval.

**What it does:**
- `GET /schema-proposals/pending` to list pending proposals
- For each proposal:
  - Shows doc_type, proposed_version, additions, relaxed_fields
  - Shows origin document filename and a link to it
  - Approve / Reject buttons (with rejection reason text input)
- `POST /schema-proposals/{id}/approve`
- `POST /schema-proposals/{id}/reject` with `{reason: str}`

---

### pages/5_Analytics.py — Analytics

Aggregate metrics visualisation.

**What it does:**
- `GET /analytics/` for all metrics
- Bar chart: document counts by status
- Bar chart: resolution strategy usage
- Bar chart: verifier failures by verifier name
- Line/bar chart: average confidence by agent
- Key metrics as `st.metric()` cards:
  - Acceptance rate, HITL rate, retry rate
  - Total documents processed

---

### pages/6_Knowledge_Map.py — Knowledge Map

Force-directed retrieval graph.

**What it does:**
- `GET /knowledge-graph/?limit=50`
- Renders using `streamlit-agraph`:
  - Nodes: documents (colored by `doc_type`)
  - Edges: retrieval log entries (width proportional to similarity_score)
  - Node label: filename
- Hover: shows document metadata
- Click: links to document detail

---

## api_client.py

Typed HTTP client used by all pages:

```python
class ApiClient:
    def __init__(self, base_url: str): ...

    def ingest(self, file_bytes: bytes, filename: str) -> dict: ...
    def get_document(self, document_id: str) -> dict: ...
    def list_documents(self, status: str | None, doc_type: str | None,
                       limit: int, offset: int) -> list[dict]: ...
    def get_document_timeline(self, document_id: str) -> list[dict]: ...
    def get_document_explain(self, document_id: str) -> dict: ...
    def get_document_similar(self, document_id: str, top_k: int) -> list[dict]: ...
    def search(self, query: str, doc_type: str | None, top_k: int) -> list[dict]: ...
    def get_analytics(self) -> dict: ...
    def get_review_pending(self) -> list[dict]: ...
    def submit_review_decision(self, document_id: str, approved: bool, corrections: dict) -> dict: ...
    def get_schema_proposals(self) -> list[dict]: ...
    def approve_schema_proposal(self, proposal_id: str) -> dict: ...
    def reject_schema_proposal(self, proposal_id: str, reason: str) -> dict: ...
    def get_knowledge_graph(self, limit: int) -> dict: ...
    def query(self, question: str) -> dict: ...
```

All methods raise `ApiError` on non-2xx responses. Pages catch `ApiError` and
call `st.error(str(e))`.

---

## Smoke tests

`frontend/tests/test_smoke.py` — headless tests using Streamlit's `AppTest`:

```python
def test_upload_page_renders():
    at = AppTest.from_file("frontend/app.py")
    with mock.patch("api_client.client", ...):
        at.run()
    assert not at.exception

def test_documents_page_renders():
    at = AppTest.from_file("frontend/pages/1_Documents.py")
    with mock.patch("api_client.client.list_documents", return_value=[...]):
        at.run()
    assert not at.exception
```

One smoke test per page. API calls are mocked — no running backend required.

```bash
make test-smoke
# → pytest frontend/tests/ -v
```

---

## Dark theme

`frontend/.streamlit/config.toml`:

```toml
[theme]
base = "dark"
primaryColor = "#4F8BF9"
backgroundColor = "#0E1117"
secondaryBackgroundColor = "#262730"
textColor = "#FAFAFA"
font = "sans serif"
```

---

## Running

In Docker (automatic via `docker-compose.yml`):
```bash
make up   # frontend starts at http://localhost:8501
```

Locally against a running API:
```bash
make dashboard
# → API_BASE_URL=http://localhost:8000 streamlit run frontend/app.py
```
