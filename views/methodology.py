"""Metric definitions, selected-vs-alternative MRR, data dictionary and provenance."""
import pandas as pd
import streamlit as st

from src import config
from src.data.schema import TABLES
from src.metrics.calculations import mrr_definitions
from src.metrics.definitions import METRICS, STATUS_ORDER
from src.ui.components import dataset_banner, page_setup

con, source = page_setup("Methodology", "📐")
st.title("Methodology and metric definitions")
dataset_banner(source)

st.header("Selected MRR definition vs alternatives")
st.write("The dataset stores about ten subscription rows per account. Whether those rows are concurrent line items or successive billing periods "
         "cannot be determined from the data, and the answer moves MRR by roughly 7x. The dashboard uses definition 1; the others are shown for transparency.")
d = mrr_definitions(con)
st.dataframe(d.df.style.format({"mrr": "${:,.0f}", "arr (12 x MRR)": "${:,.0f}"}), width="stretch", hide_index=True)
st.caption(d.headline)

st.header("Metric registry")
st.write("Status: **available** (direct), **caveated** (computed but a known data issue changes interpretation), **approximate** (proxy), **unavailable** (not computed).")
status_f = st.multiselect("Status", STATUS_ORDER, default=STATUS_ORDER)
for m in METRICS.values():
    if m.status not in status_f:
        continue
    badge = {"available": "🟢", "caveated": "🟠", "approximate": "🟡", "unavailable": "⚪"}[m.status]
    with st.expander(f"{badge} {m.name}  ({m.status})"):
        st.markdown(f"**Formula:** {m.formula}")
        st.markdown(f"**Date basis:** {m.date_basis}")
        st.markdown(f"**Grain:** {m.grain}")
        st.markdown(f"**Filters:** {m.filters}")
        st.markdown(f"**Assumptions:** {m.assumptions}")
        st.markdown(f"**Limitations:** {m.limitations}")
        if m.related_dq:
            st.caption("Related data-quality issues: " + ", ".join(m.related_dq))

st.header("Data dictionary (raw files)")
for t in TABLES.values():
    with st.expander(f"{t.name} - {t.filename}"):
        st.dataframe(pd.DataFrame({"column": list(t.columns), "parsed as": list(t.columns.values()), "primary key": [c == t.pk for c in t.columns]}), width="stretch", hide_index=True)

st.header("Provenance")
st.markdown(f"- Dataset: **{config.DATASET_NAME}** by {config.DATASET_AUTHOR} - [{config.DATASET_URL}]({config.DATASET_URL})\n"
            "- The supplied ZIP contained only the five CSVs: no README or licence. Synthetic origin is based on the dataset's public listing and placeholder company names; it is not confirmed by the files.\n"
            "- Verify the licence on the Kaggle page before redistributing data. Credit the author.\n"
            "- Raw files are never modified; checksums are recorded in `data/processed/raw_manifest.json`.")
