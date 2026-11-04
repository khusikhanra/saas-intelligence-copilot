"""Deterministic, evidence-based insight generation. Separates facts, metrics, explanations, recommendations, limitations.
Every sentence is built from computed results; nothing here calls a language model."""
from __future__ import annotations

import pandas as pd

from src.analytics.dq_warnings import load_audit
from src.metrics import calculations as C
from src.metrics.calculations import AnalysisResult, Filters, NO_FILTERS


def build_insights(con, f: Filters = NO_FILTERS) -> dict:
    k = C.kpis(con, f)
    defs = C.mrr_definitions(con)
    facts, explanations, recs, limits, rows = [], [], [], [], []

    facts.append(f"MRR on {k['as_of']} is ${k['mrr']:,.0f} across {k['active_paying_accounts']:,} accounts "
                 f"({'n/a' if k['mom_pct'] is None else f'{100 * k['mom_pct']:.1f}%'} vs one month earlier).")
    lo, hi = defs.df["mrr"].min(), defs.df["mrr"].max()
    facts.append(f"That figure depends heavily on definition: alternatives give ${lo:,.0f} to ${hi:,.0f}.")
    rows.append({"metric": "MRR (selected definition)", "value": f"${k['mrr']:,.0f}"})
    rows.append({"metric": "ARR run-rate (12 x MRR)", "value": f"${k['arr']:,.0f}"})
    rows.append({"metric": "Account churn rate (churn_flag)", "value": "n/a" if k["account_churn_rate"] is None else f"{100 * k['account_churn_rate']:.1f}%"})
    rows.append({"metric": "Accounts with >=1 churn event", "value": f"{k['accounts_with_churn_event']:,} of {k['accounts']:,}"})

    best_seg = None
    significant_any = False
    for dim in ("industry", "referral_source", "country", "plan_tier", "seats_band"):
        r = C.churn_by_segment(con, dim, f)
        if r.df.empty:
            continue
        top = r.df.sort_values("churn_rate", ascending=False).iloc[0]
        p = r.stats.get("p_value")
        if p is not None and p == p:
            if p < 0.10:
                significant_any = True
                if best_seg is None or p < best_seg[0]:
                    best_seg = (p, dim, top[dim], top["churn_rate"], int(top["accounts"]))
            facts.append(f"By {C.ACCOUNT_DIMENSIONS[dim]}: highest observed churn is {top[dim]} ({100 * top['churn_rate']:.1f}%, n={int(top['accounts'])}); p = {p:.3f}.")

    reasons = C.churn_reasons(con, f)
    if not reasons.df.empty:
        facts.append(reasons.headline)
    assoc_p = {}
    for fn, metric, name in ((C.support_vs_churn, "avg_first_response_min", "support response time"), (C.usage_vs_churn, "total_usage_count", "feature usage")):
        r = fn(con, metric, f)
        if r.stats:
            facts.append(r.headline)
            assoc_p[name] = r.stats.get("p_value")
    unusual = C.unusual_changes(con, f)
    facts += unusual.facts
    series = C.monthly_series(con, f)
    ended_total = series["ended_subs_all"].sum()
    late_share = series["ended_subs_all"].tail(3).sum() / ended_total if ended_total else 0.0

    if best_seg:
        p, dim, val, rate, n = best_seg
        explanations.append(f"{val} ({C.ACCOUNT_DIMENSIONS[dim]}) shows the strongest, though still not conclusive, churn difference (p = {p:.3f}, n={n}). "
                            "Possible reasons (untested here): product fit, pricing sensitivity, or acquisition quality for that segment.")
    if late_share > 0.5:
        explanations.append(f"{100 * late_share:.0f}% of subscription endings fall in the final three months of data; this could be a real event, "
                            "a reporting/data-generation artifact, or the cumulative effect of a fast-growing base. The dataset cannot distinguish these.")
    weak = [n for n, pv in assoc_p.items() if pv is not None and pv == pv and pv >= 0.10]
    strong = [n for n, pv in assoc_p.items() if pv is not None and pv == pv and pv < 0.10]
    if weak:
        explanations.append(f"{' and '.join(weak).capitalize()} show no statistically reliable relationship with churn here, which may mean they are not drivers in this data, "
                            "or that undated lifetime aggregates cannot detect timing effects.")
    if strong:
        explanations.append(f"{' and '.join(strong).capitalize()} show a suggestive association with churn; the data cannot tell whether it comes before churn or is a consequence of it.")

    recs.append("Define one churn source of truth (status + churn date) before any retention spending decision: the flag and the events disagree for most accounts (DQ-01).")
    if significant_any and best_seg:
        recs.append(f"Run a small qualitative review (exit interviews, win/loss notes) of {best_seg[2]} accounts to test whether the observed churn gap is real before changing strategy.")
    else:
        recs.append("Do not allocate segment-specific retention budget on this data: no segment difference is statistically reliable.")
    if late_share > 0.5:
        recs.append("Verify whether the late-period spike in endings and churn events is real (billing-system cross-check) before treating churn as 'increasing'.")
    recs.append("Instrument missing data: invoices/payments (cash revenue), dated churn status, time-aligned user-level usage, and spend by channel, so MRR, LTV, CAC and early-warning signals can be measured.")
    recs.append("Use the watchlist (high-MRR accounts with recent churn events) for account-manager outreach as a low-cost action; it is a rule, not a prediction.")

    high = [i for i in load_audit()["issues"] if i["severity"] == "High"]
    limits += [f"{len(high)} high-severity data-quality issues are open (see Data Quality page); the most important are conflicting churn indicators, concurrent subscription rows and undated usage.",
               "All figures describe recorded subscription amounts, not cash revenue; LTV, CAC, NRR and trial conversion are unavailable.",
               "Associations are observational and cumulative since signup; no causal claims are supported.",
               "The dataset is described by its public listing as synthetic; findings illustrate method, not a real business."]
    return {"facts": facts, "metrics": pd.DataFrame(rows), "explanations": explanations, "recommendations": recs, "limitations": limits}


def insights_result(con, f: Filters = NO_FILTERS) -> AnalysisResult:
    ins = build_insights(con, f)
    df = pd.DataFrame({"recommendation": ins["recommendations"]})
    return AnalysisResult("business_insights", "Business insights and recommendations", df,
        headline="Evidence-based summary built from computed results. Main message: the data supports reporting and hypotheses, not strong causal conclusions or segment-specific action.",
        facts=ins["facts"], method="src/analytics/insights.py (aggregates the trusted analyses above)",
        caveats=ins["limitations"], metric_ids=["mrr", "account_churn_rate_flag", "churn_reasons"],
        tables=["accounts", "subscriptions", "churn_events"], stats={"sections": ins})


def report_markdown(ins: dict, title: str = "SaaS Intelligence Copilot - Summary Report") -> str:
    L = [f"# {title}", "", "_Generated from the supplied RavenStack dataset (described as synthetic). Recorded subscription amounts, not audited financials._", ""]
    L += ["## Key metrics", ""] + [f"- **{r['metric']}**: {r['value']}" for _, r in ins["metrics"].iterrows()]
    for name, key in (("Observed facts", "facts"), ("Possible explanations (hypotheses)", "explanations"), ("Recommendations", "recommendations"), ("Limitations and missing evidence", "limitations")):
        L += ["", f"## {name}", ""] + [f"- {x}" for x in ins[key]]
    return "\n".join(L) + "\n"
