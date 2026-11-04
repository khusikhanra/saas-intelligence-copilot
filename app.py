"""SaaS Intelligence Copilot - entry point. Run with:  streamlit run app.py"""
import streamlit as st

st.set_page_config(page_title="SaaS Intelligence Copilot", page_icon="📊", layout="wide")

pages = st.navigation([
    st.Page("views/overview.py", title="Overview", icon="📊", default=True),
    st.Page("views/copilot.py", title="AI Copilot", icon="🤖"),
    st.Page("views/data_quality.py", title="Data Quality", icon="🧪"),
    st.Page("views/methodology.py", title="Methodology", icon="📐"),
])
with st.sidebar:
    st.markdown("### SaaS Intelligence Copilot")
pages.run()
