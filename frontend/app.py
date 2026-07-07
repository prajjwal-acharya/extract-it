"""Doc Intel Platform — navigation router."""

import streamlit as st

st.set_page_config(page_title="Doc Intel Platform", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
<style>
[data-testid="stSidebar"] { background-color: #0f1117; }
h1 { font-weight: 600; letter-spacing: -0.5px; }
h2, h3 { font-weight: 500; }
.stMetric label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: #888; }
</style>
""",
    unsafe_allow_html=True,
)

pg = st.navigation(
    {
        "": [
            st.Page("pages/0_Home.py", title="Home", default=True),
        ],
        "Pipeline": [
            st.Page("pages/1_Documents.py", title="Documents"),
            st.Page("pages/2_Search.py", title="Search"),
            st.Page("pages/3_Review_Queue.py", title="Review Queue"),
        ],
        "Governance": [
            st.Page("pages/4_Schema_Proposals.py", title="Schema Proposals"),
            st.Page("pages/5_Analytics.py", title="Analytics"),
            st.Page("pages/6_Knowledge_Map.py", title="Knowledge Map"),
        ],
    }
)

pg.run()
