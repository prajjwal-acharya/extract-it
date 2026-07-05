"""Documents page — list, detail, timeline, explain, similar docs."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import streamlit as st
import pandas as pd

from api_client import ApiError, client

st.set_page_config(page_title="Documents · Doc Intel", layout="wide")
st.title("📋 Documents")

# ── Filters ──────────────────────────────────────────────────────────────────

STATUSES = [
    "",
    "completed",
    "failed",
    "rejected",
    "pending",
    "awaiting_review",
    "persist_failed",
    "verification_failed",
]
DOC_TYPES = ["", "passport", "bank_statement", "invoice", "id_card", "driving_license"]

col1, col2, col3, col4 = st.columns([2, 2, 1, 1])
status_filter = col1.selectbox("Status", STATUSES)
doc_type_filter = col2.selectbox("Doc type", DOC_TYPES)
limit = col3.number_input("Limit", min_value=10, max_value=200, value=50, step=10)
if col4.button("↻ Refresh"):
    st.rerun()

# ── Document table ────────────────────────────────────────────────────────────

try:
    docs = client.list_documents(
        status=status_filter or None,
        doc_type=doc_type_filter or None,
        limit=int(limit),
    )
except ApiError as e:
    st.error(f"Could not load documents: {e}")
    docs = []

if not docs:
    st.info("No documents found. Upload one from the home page.")
    st.stop()

# Enrich table with confidence scores from logs
rows = []
for d in docs:
    rows.append(
        {
            "id": d["id"],
            "filename": d["filename"],
            "doc_type": d.get("doc_type") or "—",
            "status": d.get("status") or "—",
            "current_phase": d.get("current_phase") or "—",
            "created_at": d.get("created_at") or "—",
        }
    )

df = pd.DataFrame(rows)
st.dataframe(
    df,
    use_container_width=True,
    column_config={
        "id": st.column_config.TextColumn("ID", width="medium"),
        "filename": st.column_config.TextColumn("Filename", width="large"),
        "doc_type": st.column_config.TextColumn("Type", width="small"),
        "status": st.column_config.TextColumn("Status", width="small"),
        "current_phase": st.column_config.TextColumn("Phase", width="small"),
        "created_at": st.column_config.TextColumn("Created", width="medium"),
    },
)

st.caption(f"{len(docs)} documents")

# ── Document selection ────────────────────────────────────────────────────────

doc_ids = [d["id"] for d in docs]
doc_labels = {f"{d['filename']} · {d['id'][:8]}": d["id"] for d in docs}

pre_selected = st.session_state.get("selected_doc_id", "")
default_label = next((lbl for lbl, id_ in doc_labels.items() if id_ == pre_selected), "")
label_options = ["— select —"] + list(doc_labels.keys())
default_idx = label_options.index(default_label) if default_label in label_options else 0

selected_label = st.selectbox("Inspect document", label_options, index=default_idx)
if selected_label == "— select —":
    st.stop()

selected_id = doc_labels[selected_label]
st.session_state["selected_doc_id"] = selected_id

# ── Document detail tabs ──────────────────────────────────────────────────────

try:
    detail = client.get_document(selected_id)
except ApiError as e:
    st.error(f"Could not load document: {e}")
    st.stop()

tab_overview, tab_timeline, tab_explain, tab_similar = st.tabs(
    ["📄 Overview", "⏱ Timeline", "🔎 Explain", "🔗 Similar"]
)

# ── Tab: Overview ─────────────────────────────────────────────────────────────

with tab_overview:
    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Status", detail.get("status") or "—")
    col_b.metric("Phase", detail.get("current_phase") or "—")
    col_c.metric("Doc type", detail.get("doc_type") or "—")

    # Confidence logs
    logs = detail.get("confidence_logs") or []
    if logs:
        st.markdown("**Confidence signals**")
        metric_cols = st.columns(min(len(logs), 5))
        for i, cl in enumerate(logs):
            metric_cols[i % 5].metric(
                cl["agent"], f"{cl['score']:.3f}", help=cl.get("reason") or ""
            )

    col_left, col_right = st.columns(2)

    with col_left:
        with st.expander("Extracted fields", expanded=True):
            st.json(detail.get("extracted_fields") or {})

        with st.expander("Universal schema"):
            st.json(detail.get("universal_schema") or {})

    with col_right:
        truth = detail.get("truth_report")
        if truth:
            with st.expander("Truth Engine report", expanded=True):
                c1, c2 = st.columns(2)
                c1.metric("Confidence", f"{truth.get('final_confidence', 0):.3f}")
                c2.metric("Coverage", f"{truth.get('coverage_score', 0):.3f}")
                st.caption(truth.get("decision_reason") or "")

                if truth.get("verification_reports"):
                    st.markdown("**Verifier results**")
                    for vr in truth["verification_reports"]:
                        icon = "✅" if vr.get("passed") else "❌"
                        st.markdown(
                            f"{icon} **{vr['verifier_name']}** — "
                            f"score `{vr.get('confidence', 0):.2f}`"
                        )

                if truth.get("required_fields_missing"):
                    st.warning(
                        f"Missing required fields: {', '.join(truth['required_fields_missing'])}"
                    )
                if truth.get("additional_fields"):
                    st.info(f"Additional fields found: {', '.join(truth['additional_fields'])}")

        resolution = detail.get("resolution")
        if resolution:
            with st.expander("Resolution decision"):
                st.metric("Strategy", resolution.get("strategy") or "—")
                st.caption(resolution.get("reason") or "")
                if resolution.get("requires_human"):
                    st.warning("Human review required")

        learning = detail.get("learning")
        if learning:
            with st.expander("Learning decision"):
                cols = st.columns(2)
                cols[0].metric("Allow learning", "Yes" if learning.get("allow_learning") else "No")
                cols[1].metric(
                    "Schema candidate", "Yes" if learning.get("schema_candidate") else "No"
                )
                st.caption(learning.get("reason") or "")
                if learning.get("schema_proposal"):
                    st.json(learning["schema_proposal"])

        persist = detail.get("persistence_audit")
        if persist:
            with st.expander("Persistence audit"):
                st.metric("Persist status", persist.get("persist_status") or "—")
                if persist.get("persist_reason"):
                    st.error(persist["persist_reason"])

    if detail.get("retrieval_history"):
        with st.expander("Retrieval history"):
            st.dataframe(
                pd.DataFrame(detail["retrieval_history"]),
                use_container_width=True,
            )

# ── Tab: Timeline ─────────────────────────────────────────────────────────────

with tab_timeline:
    try:
        timeline = client.get_timeline(selected_id)
    except ApiError as e:
        st.error(f"Could not load timeline: {e}")
        timeline = []

    if not timeline:
        st.info("No timeline data available yet.")
    else:
        _EVENT_COLORS = {
            "upload": "🟦",
            "classification": "🟩",
            "extraction": "🟨",
            "truth_engine": "🟧",
            "schema_validation": "🟪",
            "persistence": "⬜",
            "human_review": "🔴",
        }

        rows = []
        for ev in timeline:
            name = ev.get("event", "")
            icon = next((v for k, v in _EVENT_COLORS.items() if name.startswith(k)), "⬛")
            rows.append(
                {
                    "": icon,
                    "Event": name,
                    "Timestamp": ev.get("timestamp") or "—",
                    "Confidence": f"{ev['confidence']:.3f}"
                    if ev.get("confidence") is not None
                    else "—",
                    "Duration ms": ev.get("duration_ms")
                    if ev.get("duration_ms") is not None
                    else "—",
                    "Strategy": ev.get("strategy") or "—",
                    "Reason": (ev.get("reason") or "")[:80],
                }
            )

        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "": st.column_config.TextColumn("", width="small"),
                "Event": st.column_config.TextColumn("Event", width="medium"),
                "Confidence": st.column_config.TextColumn("Confidence", width="small"),
                "Duration ms": st.column_config.NumberColumn("Duration ms", width="small"),
            },
        )

        # Simple confidence-over-time bar chart
        conf_data = [
            {"event": ev["event"], "confidence": ev["confidence"]}
            for ev in timeline
            if ev.get("confidence") is not None
        ]
        if conf_data:
            st.markdown("**Confidence per event**")
            chart_df = pd.DataFrame(conf_data).set_index("event")
            st.bar_chart(chart_df)

# ── Tab: Explain ──────────────────────────────────────────────────────────────

with tab_explain:
    try:
        explain = client.get_explain(selected_id)
    except ApiError as e:
        st.error(f"Could not load explanation: {e}")
        explain = {}

    if explain:
        verdict = explain.get("verdict", "unknown")
        verdict_icons = {
            "completed": "✅ Accepted",
            "rejected": "⛔ Rejected",
            "failed": "❌ Failed",
            "persist_failed": "⚠️ Persist failed",
            "verification_failed": "🔬 Verification failed",
        }
        st.subheader(verdict_icons.get(verdict, f"📄 {verdict}"))

        conf = explain.get("confidence") or {}
        col1, col2 = st.columns(2)
        if conf.get("final") is not None:
            col1.metric("Final confidence", f"{conf['final']:.3f}")
        if conf.get("coverage_score") is not None:
            col2.metric("Field coverage", f"{conf['coverage_score']:.3f}")

        if explain.get("truth_engine_reason"):
            st.info(f"**Truth Engine:** {explain['truth_engine_reason']}")

        if explain.get("planner_reasoning"):
            st.info(f"**Planner:** {explain['planner_reasoning']}")

        # Verifiers
        verifiers = explain.get("verifiers") or {}
        passed = verifiers.get("passed") or []
        failed = verifiers.get("failed") or []
        if passed or failed:
            st.markdown("**Verifier results**")
            col_p, col_f = st.columns(2)
            with col_p:
                st.success(f"Passed ({len(passed)})")
                for v in passed:
                    st.markdown(f"- ✅ {v}")
            with col_f:
                if failed:
                    st.error(f"Failed ({len(failed)})")
                    for v in failed:
                        st.markdown(f"- ❌ {v}")

        # Field coverage
        fields = explain.get("field_coverage") or {}
        missing = fields.get("missing_required") or []
        additional = fields.get("additional_discovered") or []
        if missing:
            st.warning(f"**Missing required fields:** {', '.join(missing)}")
        if additional:
            st.info(f"**Additional fields discovered:** {', '.join(additional)}")

        # Learning
        learning = explain.get("learning")
        if learning:
            st.markdown("**Learning decision**")
            action_labels = {
                "learned_from_document": "📚 Learned from document",
                "learned_from_human_correction": "🧑‍🏫 Learned from human correction",
                "learning_allowed_but_not_applied": "🔒 Learning allowed but not applied",
                "learning_blocked": "🚫 Learning blocked",
            }
            action = learning.get("action", "")
            st.markdown(action_labels.get(action, action))
            if learning.get("reason"):
                st.caption(learning["reason"])
            if learning.get("schema_candidate"):
                st.info("Schema change proposed — check Schema Proposals page")

# ── Tab: Similar ─────────────────────────────────────────────────────────────

with tab_similar:
    try:
        similar = client.get_similar(selected_id, top_k=8)
    except ApiError as e:
        st.error(f"Could not load similar documents: {e}")
        similar = []

    if not similar:
        st.info("No embeddings found for this document.")
    else:
        for s in similar:
            score = s.get("similarity_score", 0)
            with st.container():
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                c1.markdown(f"**{s.get('filename', 'unknown')}**")
                c2.metric("Score", f"{score:.3f}")
                c3.markdown(f"`{s.get('doc_type') or '—'}`")
                c4.markdown(f"`{s.get('embedding_source') or '—'}`")
            st.divider()
