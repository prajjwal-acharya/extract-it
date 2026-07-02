import streamlit as st
import requests
import json

API_BASE = "http://app:8000"

st.title("Document Review — HITL")

st.header("Upload Document")
uploaded = st.file_uploader("Choose a PDF", type=["pdf", "png", "jpg", "jpeg"])
if uploaded and st.button("Ingest"):
    resp = requests.post(f"{API_BASE}/ingest/", files={"file": (uploaded.name, uploaded, uploaded.type)})
    if resp.ok:
        st.success(f"Queued: {resp.json()['document_id']}")
    else:
        st.error(resp.text)

st.header("Natural Language Query")
question = st.text_input("Ask a question about ingested documents")
if question and st.button("Query"):
    resp = requests.post(f"{API_BASE}/query/", json={"question": question})
    if resp.ok:
        data = resp.json()
        st.write(data["answer"])
        st.caption("Sources: " + ", ".join(data["sources"]))
    else:
        st.error(resp.text)
