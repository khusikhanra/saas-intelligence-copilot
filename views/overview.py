"""SaaS Intelligence Copilot - Overview dashboard (entry point)."""
import streamlit as st

from src.analytics.dq_warnings import load_audit
from src.analytics.insights import build_insights, report_markdown
from src.metrics import calculations as C
from src.ui import charts as ch
from src.ui.components import cached_analysis, cached_kpis, cached_series, dataset_banner, page_setup, sidebar_filters

con, source = page_setup("Overview", "📊")
f = sidebar_filters(con)
st.title("SaaS Overview")
dataset_banner(source)
if f.start and f.end and f.start > f.end:
    st.error("Start date must be before the end date.")
    st.stop()
st.caption(f"Active filters: {f.describe()}")

k = cached_kpis(con, f)
if k["accounts"] == 0:
    st.warning("No accounts match the selected filters. Widen the filters in the sidebar.")
    st.stop()

def compact(x: float) -> str:
    return f"${x / 1e6:,.2f}M" if x >= 1e6 else (f"${x / 1e3:,.1f}K" if x >= 1e4 else f"${x:,.0f}")


c = st.columns(5)
c[0].metric("MRR (recorded)", compact(k["mrr"]), None if k["mom_pct"] is None else f"{100 * k['mom_pct']:.1f}% MoM",
            help=f"${k['mrr']:,.0f}. Sum of mrr_amount over paid subscriptions open on the as-of date. See Methodology for alternatives.")
c[1].metric("ARR run-rate", compact(k["arr"]), help=f"${k['arr']:,.0f}. 12 x MRR. Not contracted annual value or cash.")
c[2].metric("Active paying accounts", f"{k['active_paying_accounts']:,}")
c[3].metric("Active paid subscriptions", f"{k['active_paid_subs']:,}")
c[4].metric("Account churn (flag)", "n/a" if k["account_churn_rate"] is None else f"{100 * k['account_churn_rate']:.1f}%",
            help="Share of accounts with churn_flag = true. Conflicts with churn events for many accounts (see Data Quality).")
st.caption(f"As of {k['as_of']}. Recorded subscription amounts, not cash revenue. The MRR figure depends on how subscription rows are interpreted; the Methodology page shows the spread.")

tab_rev, tab_sub, tab_churn, tab_seg, tab_sup, tab_ins = st.tabs(["Revenue", "Subscriptions", "Churn & retention", "Segments", "Support & usage", "Insights & export"])
series = cached_series(con, f)

with tab_rev:
    if series.empty:
        st.info("No data in the selected range.")
    else:
        l, r = st.columns(2)
        l.plotly_chart(ch.make_chart(series, {"kind": "line", "x": "month", "y": "mrr"}, "MRR at month end"), width="stretch")
        r.plotly_chart(ch.mrr_waterfall_bars(series), width="stretch")
        st.caption("New MRR counts every newly started paid subscription row; churned MRR counts opening subscriptions that ended in the month (gross; no expansion/contraction).")
with tab_sub:
    if not series.empty:
        l, r = st.columns(2)
        l.plotly_chart(ch.make_chart(series, {"kind": "line", "x": "month", "y": "active_paid_subs"}, "Active paid subscriptions"), width="stretch")
        r.plotly_chart(ch.make_chart(series, {"kind": "bar", "x": "month", "y": "new_subs"}, "New paid subscriptions"), width="stretch")
with tab_churn:
    l, r = st.columns(2)
    ser2 = series[series["opening_subs"] >= 30] if not series.empty else series
    if not ser2.empty:
        l.plotly_chart(ch.make_chart(ser2, {"kind": "line", "x": "month", "y": "gross_mrr_churn_rate"}, "Gross MRR churn rate (months with >= 30 opening subscriptions)"), width="stretch")
    coh = cached_analysis(con, "cohort_retention", (), f)
    fig = ch.make_chart(coh.df, coh.chart, "Cohort retention (no churn event since signup)")
    if fig:
        r.plotly_chart(fig, width="stretch")
    st.caption("Churn spikes in the final quarter of the data, and calendar time confounds cohort comparison. See Data Quality (DQ-16).")
    dim = st.selectbox("Observed account churn by", list(C.ACCOUNT_DIMENSIONS), format_func=lambda d: C.ACCOUNT_DIMENSIONS[d])
    res = cached_analysis(con, "churn_by_segment", (("dimension", dim),), f)
    if not res.df.empty:
        st.plotly_chart(ch.churn_bar_with_ci(res.df, dim, f"Churn rate by {C.ACCOUNT_DIMENSIONS[dim]}"), width="stretch")
        st.write(res.headline)
        for fact in res.facts:
            st.markdown(f"- {fact}")
with tab_seg:
    d2 = st.selectbox("MRR by", list(C.REVENUE_DIMENSIONS), format_func=lambda d: C.REVENUE_DIMENSIONS[d], key="revdim")
    rb = cached_analysis(con, "revenue_breakdown", (("dimension", d2),), f)
    if not rb.df.empty:
        l, r = st.columns([3, 2])
        l.plotly_chart(ch.make_chart(rb.df, rb.chart, f"MRR by {C.REVENUE_DIMENSIONS[d2]}"), width="stretch")
        r.dataframe(rb.df.style.format({"mrr": "${:,.0f}", "share_of_mrr": "{:.1%}"}), width="stretch", hide_index=True)
with tab_sup:
    l, r = st.columns(2)
    sp = cached_analysis(con, "support_summary", (), f)
    if not sp.df.empty:
        l.plotly_chart(ch.make_chart(sp.df, sp.chart, "Average resolution time by priority (hours)"), width="stretch")
        l.dataframe(sp.df, width="stretch", hide_index=True)
    fu = cached_analysis(con, "feature_usage_summary", (), f)
    if not fu.df.empty:
        r.plotly_chart(ch.make_chart(fu.df, fu.chart, "Most used features"), width="stretch")
    for fn, metric in (("support_vs_churn", "avg_first_response_min"), ("usage_vs_churn", "total_usage_count")):
        a = cached_analysis(con, fn, (("metric", metric),), f)
        st.markdown(f"**{a.title}.** {a.headline}")
with tab_ins:
    ins = build_insights(con, f)
    st.subheader("Observed facts")
    for x in ins["facts"]:
        st.markdown(f"- {x}")
    st.subheader("Possible explanations (hypotheses, not findings)")
    for x in ins["explanations"]:
        st.markdown(f"- {x}")
    st.subheader("Recommendations")
    for x in ins["recommendations"]:
        st.markdown(f"- {x}")
    st.subheader("Limitations and missing evidence")
    for x in ins["limitations"]:
        st.markdown(f"- {x}")
    st.download_button("Download summary report (.md)", report_markdown(ins), file_name="saas_copilot_report.md", mime="text/markdown")
    st.caption("Not an audited financial statement.")

high = [i for i in load_audit()["issues"] if i["severity"] == "High"]
if high:
    st.warning(f"{len(high)} high-severity data-quality issues affect these figures. See the Data Quality page.", icon="⚠️")
