"""Semantic search page — POST /search."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import streamlit as st

from api_client import ApiError, client

st.set_page_config(page_title="Search · Doc Intel", layout="wide")
st.title("🔍 Semantic Search")
st.caption("Find documents by meaning, not just keywords — powered by pgvector cosine similarity")

# ── Search form ───────────────────────────────────────────────────────────────

with st.form("search_form"):
    query = st.text_input("Query", placeholder="e.g. passport issued in 2023 with MRZ data")
    col1, col2, col3 = st.columns([3, 1, 1])
    doc_type = col2.text_input("Doc type (optional)", placeholder="passport")
    top_k = col3.number_input("Top K", min_value=1, max_value=20, value=5)
    submitted = st.form_submit_button("🔍 Search", type="primary")

if not submitted or not query.strip():
    if not submitted:
        st.info("Enter a query and press Search.")
    st.stop()

# ── Execute search ────────────────────────────────────────────────────────────

with st.spinner("Searching…"):
    try:
        results = client.search(
            query=query.strip(),
            doc_type=doc_type.strip() or None,
            top_k=int(top_k),
        )
    except ApiError as e:
        st.error(f"Search failed: {e}")
        st.stop()

if not results:
    st.warning("No results found.")
    st.stop()

st.success(f"{len(results)} result(s)")

# ── Results ───────────────────────────────────────────────────────────────────

for i, r in enumerate(results):
    score = r.get("similarity_score", 0)
    excerpt = r.get("excerpt", "")
    source = r.get("embedding_source") or "document"

    with st.container():
        col_score, col_meta, col_badge = st.columns([1, 4, 1])

        # Similarity gauge
        col_score.metric("Score", f"{score:.3f}")
        col_score.progress(min(score, 1.0))

        with col_meta:
            st.markdown(f"### {r.get('filename', 'unknown')}")
            st.markdown(
                f"`{r.get('doc_type') or '—'}` · `{r.get('status') or '—'}` · "
                f"id: `{r.get('document_id', '')[:12]}…`"
            )
            if excerpt:
                # Highlight query terms in excerpt (simple case-insensitive)
                highlighted = excerpt
                for word in query.split():
                    if len(word) > 3:
                        highlighted = highlighted.replace(
                            word, f"**{word}**"
                        )
                st.markdown(f"> {highlighted}")

        # Embedding source badge
        source_color = "🟡" if source == "hitl_correction" else "🔵"
        col_badge.markdown(f"{source_color}  \n`{source}`")

        if st.button("Open details →", key=f"open_{r['document_id']}"):
            st.session_state["selected_doc_id"] = r["document_id"]
            st.switch_page("pages/1_📋_Documents.py")

    st.divider()
