"""Knowledge Map — retrieval graph visualization."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import streamlit as st

from api_client import ApiError, client

st.set_page_config(page_title="Knowledge Map · Doc Intel", layout="wide")
st.title("🗺 Knowledge Map")
st.caption(
    "Documents connected by RAG retrieval usage — actual retrieval events, not synthetic similarity"
)

_DOC_TYPE_COLORS = [
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
]


def _color_for_doc_type(doc_type: str | None) -> str:
    if not doc_type:
        return "#aaaaaa"
    return _DOC_TYPE_COLORS[hash(doc_type) % len(_DOC_TYPE_COLORS)]


col1, col2 = st.columns([1, 1])
limit = col1.slider("Max documents", 10, 200, 50, step=10)
if col2.button("↻ Refresh"):
    st.rerun()

# ── Load graph data ───────────────────────────────────────────────────────────

try:
    graph_data = client.get_knowledge_graph(limit=limit)
except ApiError as e:
    st.error(f"Could not load knowledge graph: {e}")
    st.stop()

nodes_data = graph_data.get("nodes", [])
edges_data = graph_data.get("edges", [])

if not nodes_data:
    st.info("No documents ingested yet.")
    st.stop()

# ── Render graph ──────────────────────────────────────────────────────────────

try:
    from streamlit_agraph import Config, Edge, Node, agraph

    nodes = [
        Node(
            id=n["id"],
            label=n.get("filename", n["id"])[:20],
            color=_color_for_doc_type(n.get("doc_type")),
            title=f"{n.get('doc_type', 'unknown')} | {n.get('status', '')}",
            size=20,
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
    st.warning(
        "streamlit-agraph not installed — showing raw data. "
        "Install it with `pip install streamlit-agraph`."
    )
    import pandas as pd  # type: ignore[import-untyped]

    col_n, col_e = st.columns(2)
    col_n.subheader("Nodes")
    col_n.dataframe(pd.DataFrame(nodes_data), use_container_width=True)
    col_e.subheader("Edges")
    col_e.dataframe(pd.DataFrame(edges_data), use_container_width=True)

# ── Legend ────────────────────────────────────────────────────────────────────

with st.expander("Legend"):
    st.markdown(
        "Each **node** is a document. Each **edge** represents a retrieval event: "
        "the source document used the target document as a few-shot example during extraction. "
        "Edge width reflects similarity score."
    )
