"""Doc Intel Platform — Dashboard entry point.

This page handles document upload and shows live processing status.
Navigate to other pages via the sidebar.
"""

import time

import streamlit as st

from api_client import ApiError, client

st.set_page_config(
    page_title="Doc Intel Platform",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🧠 Doc Intel Platform")
st.caption("Adaptive document extraction · powered by LLM + Truth Engine")

# ── Upload ────────────────────────────────────────────────────────────────────

st.header("Upload a document")

uploaded = st.file_uploader(
    "Choose a file (PDF, PNG, JPEG)",
    type=["pdf", "png", "jpg", "jpeg"],
    label_visibility="collapsed",
)

col_btn, col_dup = st.columns([1, 3])
ingest_btn = col_btn.button("⬆ Ingest", type="primary", disabled=uploaded is None)

if ingest_btn and uploaded is not None:
    with st.spinner("Uploading…"):
        try:
            result = client.ingest(uploaded.getvalue(), uploaded.name)
            doc_id = result["document_id"]
            if result.get("duplicate"):
                st.warning(f"Duplicate detected — reusing document `{doc_id}`")
            else:
                st.success(f"Ingested · `{doc_id}`")
            st.session_state["last_ingested"] = doc_id
        except ApiError as e:
            st.error(f"Ingest failed: {e}")
            doc_id = None

# ── Live status polling ───────────────────────────────────────────────────────

if "last_ingested" in st.session_state:
    doc_id = st.session_state["last_ingested"]
    st.divider()
    st.subheader("Processing status")

    col_id, col_refresh = st.columns([3, 1])
    col_id.code(doc_id)
    if col_refresh.button("↻ Refresh"):
        st.rerun()

    try:
        doc = client.get_document(doc_id)
        phase = doc.get("current_phase", "—")
        status = doc.get("status", "—")

        terminal = {"completed", "rejected", "failed", "persist_failed", "verification_failed"}
        in_progress = phase not in terminal

        col_phase, col_status = st.columns(2)
        col_phase.metric("Phase", phase)
        col_status.metric("Status", status)

        # Confidence summary
        logs = doc.get("confidence_logs") or []
        if logs:
            st.markdown("**Confidence signals**")
            metric_cols = st.columns(min(len(logs), 4))
            for i, cl in enumerate(logs):
                metric_cols[i % 4].metric(
                    label=cl["agent"],
                    value=f"{cl['score']:.2f}",
                    help=cl.get("reason") or "",
                )

        if status == "completed":
            st.success("✅ Pipeline completed successfully")
            if doc.get("extracted_fields"):
                with st.expander("Extracted fields"):
                    st.json(doc["extracted_fields"])
        elif status in {"failed", "persist_failed", "verification_failed"}:
            st.error(f"❌ {status}")
        elif status == "rejected":
            st.warning("⛔ Rejected by reviewer")
        elif in_progress:
            st.info(f"⏳ Processing… (phase: {phase})")
            time.sleep(3)
            st.rerun()

    except ApiError as e:
        st.error(f"Could not fetch status: {e}")

# ── Sidebar hint ─────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### Navigation")
    st.markdown(
        "- **📋 Documents** — browse & inspect all documents\n"
        "- **🔍 Search** — semantic similarity search\n"
        "- **✅ Review Queue** — pending HITL reviews\n"
        "- **🏛 Schema Proposals** — pending schema changes\n"
        "- **📊 Analytics** — pipeline metrics\n"
        "- **🗺 Knowledge Map** — retrieval graph\n"
    )
