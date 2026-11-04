"""SaaS Intelligence Copilot - entry point. Run with:  python -m streamlit run app.py"""
import streamlit as st

st.set_page_config(page_title="SaaS Intelligence Copilot", page_icon="📊", layout="wide", initial_sidebar_state="auto")

from src.ui import shell  # noqa: E402  (must follow set_page_config)

shell.apply()
pages = st.navigation([
    st.Page("views/overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page("views/copilot.py", title="AI Copilot", icon=":material/auto_awesome:"),
    st.Page("views/data_quality.py", title="Data Quality", icon=":material/fact_check:"),
    st.Page("views/methodology.py", title="Methodology", icon=":material/menu_book:"),
])
pages.run()
