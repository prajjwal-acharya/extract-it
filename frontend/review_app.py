"""Streamlit HITL review application.

Five panels: Ingest, Documents (+ phase tracker), Knowledge Map, HITL Queue, Query.
"""

import json

import requests
import streamlit as st

API_BASE = "http://app:8000"

_DOC_TYPE_COLORS = [
    "#4e79a7", "#f28e2b", "#e15759", "#76b7b2",
    "#59a14f", "#edc948", "#b07aa1", "#ff9da7",
]


def _color_for_doc_type(doc_type: str | None) -> str:
    if not doc_type:
        return "#aaaaaa"
    return _DOC_TYPE_COLORS[hash(doc_type) % len(_DOC_TYPE_COLORS)]


st.set_page_config(page_title="Doc Intel Platform", layout="wide")
st.title("Adaptive Document Intelligence — Review Console")

panel = st.sidebar.radio("Panel", ["Ingest", "Documents", "Knowledge Map", "HITL Queue", "Query"])

# ── Panel: Ingest ────────────────────────────────────────────────────────────
if panel == "Ingest":
    st.header("Upload a document")
    uploaded = st.file_uploader("Choose a file", type=["pdf", "png", "jpg", "jpeg"])
    if uploaded and st.button("Ingest"):
        try:
            resp = requests.post(
                f"{API_BASE}/ingest/",
                files={"file": (uploaded.name, uploaded.getvalue())},
                timeout=30,
            )
            resp.raise_for_status()
            st.success(f"Ingested. document_id: {resp.json()['document_id']}")
        except requests.RequestException as e:
            st.error(f"Ingest failed: {e}")

# ── Panel: Documents (+ pipeline phase tracker) ──────────────────────────────
elif panel == "Documents":
    st.header("Documents")

    col1, col2, col3 = st.columns([2, 2, 1])
    with col1:
        status_filter = st.selectbox("Filter by status", ["", "pending", "completed", "failed", "rejected"])
    with col2:
        doc_type_filter = st.text_input("Filter by doc_type")
    with col3:
        if st.button("Refresh"):
            st.rerun()

    try:
        params: dict = {"limit": 50}
        if status_filter:
            params["status"] = status_filter
        if doc_type_filter:
            params["doc_type"] = doc_type_filter
        resp = requests.get(f"{API_BASE}/documents/", params=params, timeout=10)
        resp.raise_for_status()
        docs = resp.json()
    except requests.RequestException as e:
        st.error(f"Could not load documents: {e}")
        docs = []

    if docs:
        st.dataframe(
            docs,
            column_order=["id", "filename", "doc_type", "status", "current_phase", "created_at"],
            use_container_width=True,
        )

        doc_ids = [d["id"] for d in docs]
        selected_id = st.selectbox("Inspect document", [""] + doc_ids)
        if selected_id:
            try:
                detail_resp = requests.get(f"{API_BASE}/documents/{selected_id}", timeout=10)
                detail_resp.raise_for_status()
                detail = detail_resp.json()
            except requests.RequestException as e:
                st.error(f"Could not load document detail: {e}")
                detail = None

            if detail:
                phase_col, status_col = st.columns(2)
                phase_col.metric("Current phase", detail.get("current_phase", "—"))
                status_col.metric("Status", detail.get("status", "—"))

                if detail.get("confidence_logs"):
                    st.subheader("Confidence scores")
                    for cl in detail["confidence_logs"]:
                        st.metric(
                            label=cl["agent"],
                            value=f"{cl['score']:.2f}",
                            help=cl.get("reason") or "",
                        )

                with st.expander("Extracted fields"):
                    st.json(detail.get("extracted_fields") or {})

                with st.expander("Universal schema"):
                    st.json(detail.get("universal_schema") or {})

                with st.expander("References (similar docs used during extraction)"):
                    try:
                        ref_resp = requests.get(
                            f"{API_BASE}/documents/{selected_id}/references", timeout=10
                        )
                        ref_resp.raise_for_status()
                        refs = ref_resp.json()
                        if refs:
                            st.dataframe(refs, use_container_width=True)
                        else:
                            st.info("No retrieval references recorded for this document.")
                    except requests.RequestException as e:
                        st.error(f"Could not load references: {e}")
    else:
        st.info("No documents found.")

# ── Panel: Knowledge Map ─────────────────────────────────────────────────────
elif panel == "Knowledge Map":
    st.header("Knowledge Map")

    limit = st.slider("Max documents", 10, 200, 50)
    if st.button("Refresh"):
        st.rerun()

    try:
        resp = requests.get(f"{API_BASE}/knowledge-graph/", params={"limit": limit}, timeout=15)
        resp.raise_for_status()
        graph_data = resp.json()
    except requests.RequestException as e:
        st.error(f"Could not load knowledge graph: {e}")
        graph_data = {"nodes": [], "edges": []}

    nodes_data = graph_data.get("nodes", [])
    edges_data = graph_data.get("edges", [])

    if not nodes_data:
        st.info("No documents yet — ingest some documents first.")
    else:
        try:
            from streamlit_agraph import Config, Edge, Node, agraph

            nodes = [
                Node(
                    id=n["id"],
                    label=n["filename"][:20] if n.get("filename") else n["id"][:8],
                    color=_color_for_doc_type(n.get("doc_type")),
                    title=f"{n.get('doc_type', 'unknown')} | {n.get('status', '')}",
                )
                for n in nodes_data
            ]
            edges = [
                Edge(
                    source=e["source"],
                    target=e["target"],
                    label=e.get("stage", ""),
                    width=max(1, int(e.get("similarity_score", 0.5) * 5)),
                )
                for e in edges_data
            ]
            config = Config(
                width="100%",
                height=600,
                directed=True,
                physics=True,
                hierarchical=False,
            )
            agraph(nodes=nodes, edges=edges, config=config)
            st.caption(f"{len(nodes)} nodes · {len(edges)} edges")
        except ImportError:
            st.warning("streamlit-agraph not installed — showing raw data instead.")
            st.json(graph_data)

# ── Panel: HITL Queue ────────────────────────────────────────────────────────
elif panel == "HITL Queue":
    st.header("HITL Queue — Pending Review")

    if st.button("Refresh"):
        st.rerun()

    try:
        resp = requests.get(f"{API_BASE}/review/pending", timeout=10)
        resp.raise_for_status()
        pending = resp.json()
    except requests.RequestException as e:
        st.error(f"Could not load pending reviews: {e}")
        pending = []

    if not pending:
        st.success("No documents awaiting review.")
    else:
        doc_options = {f"{d['filename']} ({d['id'][:8]})": d for d in pending}
        selected_label = st.selectbox("Select document to review", list(doc_options.keys()))
        doc = doc_options[selected_label]
        document_id = doc["id"]

        st.subheader("Extracted fields")
        st.json(doc.get("extracted_fields") or {})

        if doc.get("confidence_logs"):
            st.subheader("Confidence scores")
            for cl in doc["confidence_logs"]:
                st.metric(cl["agent"], f"{cl['score']:.2f}", help=cl.get("reason") or "")

        if doc.get("references"):
            with st.expander("Similar docs used during retry"):
                st.dataframe(doc["references"], use_container_width=True)

        with st.form("decision_form"):
            approved = st.radio("Decision", ["Approve", "Reject"]) == "Approve"
            corrections_raw = st.text_area("Corrections (JSON, optional)", "{}")
            submitted = st.form_submit_button("Submit decision")

        if submitted:
            try:
                corrections = json.loads(corrections_raw or "{}")
            except json.JSONDecodeError:
                st.error("Corrections must be valid JSON.")
                corrections = None

            if corrections is not None:
                try:
                    resp = requests.post(
                        f"{API_BASE}/review/{document_id}/decision",
                        json={"approved": approved, "corrections": corrections},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    st.success("Decision submitted.")
                    st.json(resp.json())
                except requests.RequestException as e:
                    st.error(f"Review submission failed: {e}")

# ── Panel: Query ─────────────────────────────────────────────────────────────
elif panel == "Query":
    st.header("Ask a question")
    question = st.text_input("Question")
    if question and st.button("Ask"):
        try:
            resp = requests.post(f"{API_BASE}/query/", json={"question": question}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            st.write(data.get("answer", ""))
            if data.get("sources"):
                st.caption(f"Sources: {', '.join(data['sources'])}")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 500:
                st.warning("Query not yet available — ships in P8.")
            else:
                st.error(f"Query failed: {e}")
        except requests.RequestException as e:
            st.warning("Query not yet available — ships in P8.")
            st.caption(f"({e})")
