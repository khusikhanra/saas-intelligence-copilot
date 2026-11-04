"""Data quality panel."""
import pandas as pd
import streamlit as st

from src.analytics.dq_warnings import load_audit
from src.ui.components import dataset_banner, page_setup

con, source = page_setup("Data Quality", "🧪")
st.title("Data Quality")
dataset_banner(source)
audit = load_audit()
if not audit["issues"]:
    st.warning("No audit found. Run `python -m src.data.pipeline` to generate it.")
    st.stop()
if source == "demo sample":
    st.warning("The audit below was generated from the data used at build time. If you are viewing the demo sample, re-run the pipeline on the full dataset for its audit.")

issues = pd.DataFrame(audit["issues"])
counts = issues["severity"].value_counts()
c = st.columns(4)
for col, sev in zip(c, ["High", "Medium", "Low", "Info"]):
    col.metric(f"{sev} severity", int(counts.get(sev, 0)))

sev = st.multiselect("Severity", ["High", "Medium", "Low", "Info"], default=["High", "Medium"])
for _, i in issues[issues["severity"].isin(sev)].iterrows():
    icon = {"High": "🔴", "Medium": "🟠", "Low": "🟡", "Info": "🔵"}[i["severity"]]
    scale = f"{i['affected']:,} of {i['total']:,} records ({i['pct']}%)" if i["total"] else "dataset gap"
    with st.expander(f"{icon} {i['id']} - {i['title']}  |  {scale}"):
        st.markdown(f"**What we found:** {i['description']}")
        st.markdown(f"**Impact on analysis:** {i['impact']}")
        st.markdown(f"**How it is handled:** {i['handling']}")
        if i["affects_metrics"]:
            st.caption("Affects metrics: " + ", ".join(i["affects_metrics"]))

st.subheader("Checks that passed")
st.dataframe(pd.DataFrame(audit["passed_checks"]), width="stretch", hide_index=True)
st.subheader("Join cardinality")
st.write("Why naive joins double count: each account has several child rows in every other table.")
st.json(audit["join_cardinality"])
st.subheader("Table profile")
for t in audit["profile"]:
    with st.expander(f"{t['table']} - {t['rows']:,} rows x {t['columns']} columns ({t['file']})"):
        st.dataframe(pd.DataFrame(t["profile"]), width="stretch", hide_index=True)
