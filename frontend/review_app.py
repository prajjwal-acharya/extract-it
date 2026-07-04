"""Streamlit HITL review application.

Three panels: document upload, NL query, and human review of pending
extractions (approve / reject / correct fields).
"""

import requests
import streamlit as st

API_BASE = "http://app:8000"

st.set_page_config(page_title="Doc Intel Platform", layout="wide")
st.title("Adaptive Document Intelligence — Review Console")

panel = st.sidebar.radio("Panel", ["Ingest", "Query", "Review"])

# ── Panel 1: Ingest ──────────────────────────────────────────────────────
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

# ── Panel 2: Query ───────────────────────────────────────────────────────
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

# ── Panel 3: Review (HITL) ───────────────────────────────────────────────
else:
    st.header("Pending review")
    document_id = st.text_input("Document ID")
    if document_id:
        st.subheader("Extracted fields")
        st.info("Field display wires up once P7's graph.invoke() trigger exists.")

        with st.form("decision_form"):
            approved = st.radio("Decision", ["Approve", "Reject"]) == "Approve"
            corrections_raw = st.text_area("Corrections (JSON, optional)", "{}")
            submitted = st.form_submit_button("Submit decision")

        if submitted:
            import json

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
