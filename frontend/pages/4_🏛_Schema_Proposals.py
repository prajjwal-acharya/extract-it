"""Schema Proposals — review and approve/reject pending schema changes."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import streamlit as st
import pandas as pd

from api_client import ApiError, client

st.set_page_config(page_title="Schema Proposals · Doc Intel", layout="wide")
st.title("🏛 Schema Proposals")
st.caption("Pending schema changes require human approval before activating a new SchemaVersion")

if st.button("↻ Refresh"):
    st.rerun()

# ── Load pending proposals ────────────────────────────────────────────────────

try:
    proposals = client.get_pending_proposals()
except ApiError as e:
    st.error(f"Could not load proposals: {e}")
    st.stop()

if not proposals:
    st.success("🎉 No pending schema proposals.")
    st.stop()

st.info(f"{len(proposals)} pending proposal(s)")

# ── Display and act on each proposal ─────────────────────────────────────────

for p in proposals:
    pid = p.get("id", "")
    doc_type = p.get("doc_type", "—")
    proposed_version = p.get("proposed_version", "—")
    additions = p.get("additions") or []
    relaxed = p.get("relaxed_fields") or []
    origin = p.get("origin_document_id") or "—"
    created_at = p.get("created_at") or "—"

    with st.expander(
        f"📐 `{doc_type}` → v{proposed_version}  |  {len(additions)} additions, "
        f"{len(relaxed)} relaxed  |  {created_at[:10]}",
        expanded=True,
    ):
        col_meta, col_actions = st.columns([3, 1])

        with col_meta:
            st.markdown(f"**Proposal ID:** `{pid}`")
            st.markdown(f"**Origin document:** `{origin}`")
            st.markdown(f"**Proposed version:** `{proposed_version}`")

            if additions:
                st.markdown("**New fields (additions):**")
                st.dataframe(
                    pd.DataFrame(additions),
                    use_container_width=True,
                    hide_index=True,
                )

            if relaxed:
                st.markdown("**Fields to make optional (relaxed):**")
                for f in relaxed:
                    st.markdown(f"- `{f}`")

        with col_actions:
            st.markdown("**Actions**")

            if st.button("✅ Approve", key=f"approve_{pid}", type="primary"):
                with st.spinner("Approving…"):
                    try:
                        result = client.approve_proposal(pid)
                        st.success(
                            f"Approved → new version `{result.get('new_schema_version')}`"
                        )
                        st.rerun()
                    except ApiError as e:
                        st.error(f"Approval failed: {e}")

            st.markdown("---")
            reject_reason = st.text_input(
                "Rejection reason", key=f"reason_{pid}", placeholder="not needed"
            )
            if st.button("⛔ Reject", key=f"reject_{pid}"):
                if not reject_reason.strip():
                    st.warning("Please provide a rejection reason.")
                else:
                    with st.spinner("Rejecting…"):
                        try:
                            client.reject_proposal(pid, reject_reason.strip())
                            st.success("Proposal rejected.")
                            st.rerun()
                        except ApiError as e:
                            st.error(f"Rejection failed: {e}")
