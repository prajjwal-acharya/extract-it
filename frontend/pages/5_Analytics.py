"""Analytics page — aggregate pipeline metrics and charts."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import streamlit as st
import pandas as pd  # type: ignore[import-untyped]

from api_client import ApiError, client

st.set_page_config(page_title="Analytics · Doc Intel", layout="wide")
st.title("Analytics")
st.caption("Aggregate pipeline metrics — all time")

if st.button("Refresh"):
    st.rerun()

try:
    data = client.get_analytics()
except ApiError as e:
    st.error(f"Could not load analytics: {e}")
    st.stop()

totals = data.get("totals") or {}
rates = data.get("rates") or {}
strategy_usage = data.get("strategy_usage") or {}
verifier_failures = data.get("verifier_failures") or {}
avg_confidence = data.get("avg_confidence") or {}

total_docs = totals.get("documents", 0)

st.subheader("Volume")
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total documents", total_docs)
col2.metric("Completed", totals.get("completed", 0))
col3.metric("Failed", totals.get("failed", 0))
col4.metric("Rejected", totals.get("rejected", 0))
col5.metric("Persist failed", totals.get("persist_failed", 0))

if totals.get("awaiting_review"):
    st.warning(f"{totals['awaiting_review']} document(s) awaiting human review")

st.subheader("Pipeline rates")
col_a, col_b, col_c, col_d = st.columns(4)
col_a.metric("Acceptance rate", f"{rates.get('acceptance_rate', 0):.1%}")
col_b.metric("HITL rate", f"{rates.get('hitl_rate', 0):.1%}")
col_c.metric("Retry rate", f"{rates.get('retry_rate', 0):.1%}")
col_d.metric("Schema candidate rate", f"{rates.get('schema_candidate_rate', 0):.1%}")

by_status = totals.get("by_status") or {}
if by_status:
    st.subheader("Volume by status")
    status_df = pd.DataFrame(
        {"status": list(by_status.keys()), "count": list(by_status.values())}
    ).set_index("status")
    st.bar_chart(status_df)

if strategy_usage:
    st.subheader("Strategy distribution")
    col_chart, col_table = st.columns([2, 1])
    strategy_df = (
        pd.DataFrame(
            {"strategy": list(strategy_usage.keys()), "count": list(strategy_usage.values())}
        )
        .set_index("strategy")
        .sort_values("count", ascending=False)
    )
    col_chart.bar_chart(strategy_df)
    col_table.dataframe(strategy_df, use_container_width=True)

if avg_confidence:
    st.subheader("Average confidence by agent")
    col_conf, col_tbl = st.columns([2, 1])
    conf_df = (
        pd.DataFrame(
            {"agent": list(avg_confidence.keys()), "avg_score": list(avg_confidence.values())}
        )
        .set_index("agent")
        .sort_values("avg_score")
    )
    col_conf.bar_chart(conf_df)
    col_tbl.dataframe(conf_df.style.format("{:.4f}"), use_container_width=True)

if verifier_failures:
    st.subheader("Verifier failures")
    vf_df = (
        pd.DataFrame(
            {
                "verifier": list(verifier_failures.keys()),
                "failures": list(verifier_failures.values()),
            }
        )
        .set_index("verifier")
        .sort_values("failures", ascending=False)
    )
    col_vf, col_vt = st.columns([2, 1])
    col_vf.bar_chart(vf_df)
    col_vt.dataframe(vf_df, use_container_width=True)
elif total_docs > 0:
    st.success("No verifier failures recorded.")
