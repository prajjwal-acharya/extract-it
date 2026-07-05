"""HITL Review Queue — approve, reject, or correct pending documents."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import json

import streamlit as st

from api_client import ApiError, client

st.set_page_config(page_title="Review Queue · Doc Intel", layout="wide")
st.title("✅ Review Queue")

if st.button("↻ Refresh"):
    st.rerun()

# ── Load pending reviews ──────────────────────────────────────────────────────

try:
    pending = client.get_pending_review()
except ApiError as e:
    st.error(f"Could not load review queue: {e}")
    st.stop()

if not pending:
    st.success("🎉 No documents awaiting review.")
    st.stop()

st.info(f"{len(pending)} document(s) awaiting review")

# ── Select document ───────────────────────────────────────────────────────────

doc_labels = {f"{d['filename']} · {d['id'][:8]}": d for d in pending}
selected_label = st.selectbox("Select document to review", list(doc_labels.keys()))
doc = doc_labels[selected_label]
document_id = doc["id"]

# ── Document summary ──────────────────────────────────────────────────────────

col_id, col_type, col_phase = st.columns(3)
col_id.metric("Document ID", document_id[:12] + "…")
col_type.metric("Doc type", doc.get("doc_type") or "—")
col_phase.metric("Phase", doc.get("current_phase") or "—")

# Confidence logs
conf_logs = doc.get("confidence_logs") or []
if conf_logs:
    st.markdown("**Confidence signals**")
    metric_cols = st.columns(min(len(conf_logs), 5))
    for i, cl in enumerate(conf_logs):
        metric_cols[i % 5].metric(cl["agent"], f"{cl['score']:.3f}", help=cl.get("reason") or "")

# ── Extracted fields + correction editor ─────────────────────────────────────

col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Extracted fields")
    extracted = doc.get("extracted_fields") or {}
    st.json(extracted)

with col_right:
    st.subheader("Field corrections (optional)")
    st.caption("Edit values below. Only changed fields need to be included.")

    field_corrections: dict = {}
    for field, value in extracted.items():
        new_val = st.text_input(
            field,
            value=str(value) if value is not None else "",
            key=f"field_{field}",
        )
        if new_val != str(value if value is not None else ""):
            field_corrections[field] = new_val

    st.markdown("**Or paste raw JSON corrections:**")
    corrections_json = st.text_area(
        "Raw JSON (overrides field edits above if non-empty)",
        value="{}",
        height=100,
    )

# ── References ────────────────────────────────────────────────────────────────

refs = doc.get("references") or []
if refs:
    with st.expander("Similar documents used during extraction"):
        import pandas as pd

        st.dataframe(pd.DataFrame(refs), use_container_width=True)

# ── Decision form ─────────────────────────────────────────────────────────────

st.divider()
st.subheader("Submit decision")

with st.form("review_form"):
    approved = st.radio("Decision", ["✅ Approve", "⛔ Reject"]) == "✅ Approve"
    submitted = st.form_submit_button("Submit", type="primary")

if submitted:
    # Resolve corrections — JSON text area takes precedence if non-empty
    try:
        parsed_json = json.loads(corrections_json.strip() or "{}")
    except json.JSONDecodeError:
        st.error("Raw JSON corrections are not valid JSON.")
        st.stop()

    final_corrections = parsed_json if parsed_json else field_corrections

    with st.spinner("Submitting…"):
        try:
            result = client.submit_review(
                document_id,
                approved=approved,
                corrections=final_corrections or None,
            )
            if approved:
                st.success("✅ Document approved. Pipeline will resume.")
            else:
                st.warning("⛔ Document rejected.")
            st.json(result)
            st.rerun()
        except ApiError as e:
            st.error(f"Submission failed: {e}")
