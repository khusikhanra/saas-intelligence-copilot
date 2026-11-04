"""Trusted analytical functions. Every number the dashboard and copilot show comes from here (or from
a validated read-only SQL query), never from a language model.

Each function returns an AnalysisResult with: the data, a deterministic headline + facts, the SQL/method
used, caveats, and which tables/metrics were involved (used for data-quality warnings).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import duckdb
import numpy as np
import pandas as pd

from .definitions import METRICS, unavailable_metrics
from .stats import chi_square_churn, describe_p, mann_whitney, wilson_interval

# ------------------------------------------------------------------------------------------------------
# Result container + filters
# ------------------------------------------------------------------------------------------------------


@dataclass
class AnalysisResult:
    id: str
    title: str
    df: pd.DataFrame
    headline: str = ""
    facts: list = field(default_factory=list)
    method: str = ""
    caveats: list = field(default_factory=list)
    metric_ids: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    chart: dict | None = None  # {"kind": "line|bar|heatmap|...", "x":..., "y":..., "color":...}
    stats: dict = field(default_factory=dict)


ACCOUNT_DIMENSIONS = {
    "industry": "industry", "country": "country", "referral_source": "referral source",
    "plan_tier": "account plan (as recorded)", "seats_band": "seat band",
}
REVENUE_DIMENSIONS = {"plan_tier": "subscription plan", "industry": "industry", "country": "country", "referral_source": "referral source"}
SUPPORT_METRICS = {"avg_first_response_min": "average first response (minutes)", "avg_resolution_hours": "average resolution time (hours)",
                   "n_tickets": "number of tickets", "avg_csat": "average CSAT"}
USAGE_METRICS = {"total_usage_count": "total usage count", "distinct_features": "distinct features used",
                 "usage_events": "number of usage records", "error_count": "total errors"}


@dataclass(frozen=True)
class Filters:
    start: date | None = None
    end: date | None = None
    industries: tuple = ()
    countries: tuple = ()
    referral_sources: tuple = ()
    plans: tuple = ()

    def _cats(self, alias: str, plan_col: str) -> tuple[list, list]:
        parts, params = [], []
        for col, vals in (("industry", self.industries), ("country", self.countries),
                          ("referral_source", self.referral_sources), (plan_col, self.plans)):
            if vals:
                parts.append(f"{alias}.{col} IN ({', '.join('?' for _ in vals)})")
                params += list(vals)
        return parts, params

    def sub_where(self, alias: str = "s") -> tuple[str, list]:
        """Categorical filters for subscription-grain queries (plan = subscription plan)."""
        parts, params = self._cats(alias, "plan_tier")
        return ("".join(f" AND {p}" for p in parts), params)

    def acct_where(self, alias: str = "a") -> tuple[str, list]:
        """Filters for account-grain queries (plan = account plan as recorded; dates filter signup_date)."""
        parts, params = self._cats(alias, "plan_tier")
        if self.start:
            parts.append(f"{alias}.signup_date >= ?")
            params.append(self.start)
        if self.end:
            parts.append(f"{alias}.signup_date <= ?")
            params.append(self.end)
        return ("".join(f" AND {p}" for p in parts), params)

    def describe(self) -> str:
        bits = []
        for label, vals in (("industry", self.industries), ("country", self.countries), ("channel", self.referral_sources), ("plan", self.plans)):
            if vals:
                bits.append(f"{label}: {', '.join(vals)}")
        if self.start or self.end:
            bits.append(f"dates: {self.start or 'start'} to {self.end or 'end'}")
        return "; ".join(bits) if bits else "no filters"


NO_FILTERS = Filters()


def _df(con, sql: str, params: list | None = None) -> pd.DataFrame:
    cur = con.cursor()
    try:
        return cur.execute(sql, params or []).fetchdf()
    finally:
        cur.close()


def snapshot_date(con) -> date:
    v = _df(con, "SELECT value FROM _meta WHERE key = 'snapshot_date'").iloc[0, 0]
    return datetime.strptime(str(v), "%Y-%m-%d").date()


def data_start(con) -> date:
    v = _df(con, "SELECT least((SELECT min(start_date) FROM subscriptions), (SELECT min(signup_date) FROM accounts)) AS d").iloc[0, 0]
    return pd.Timestamp(v).date()


def filter_options(con) -> dict:
    out = {}
    for col, tbl in (("industry", "accounts"), ("country", "accounts"), ("referral_source", "accounts"), ("plan_tier", "subscriptions")):
        out[col] = sorted(_df(con, f"SELECT DISTINCT {col} AS v FROM {tbl} ORDER BY 1")["v"].tolist())
    return out


def _pct(x, d=1) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.{d}f}%"


def _money(x) -> str:
    return f"${x:,.0f}"


# ------------------------------------------------------------------------------------------------------
# MRR / revenue
# ------------------------------------------------------------------------------------------------------
_ACTIVE = "s.start_date <= ? AND (s.end_date IS NULL OR s.end_date > ?)"


def mrr_at(con, as_of: date | None = None, f: Filters = NO_FILTERS) -> dict:
    as_of = as_of or f.end or snapshot_date(con)
    w, p = f.sub_where("s")
    sql = f"""SELECT coalesce(sum(s.mrr_amount), 0) AS mrr, count(*) AS active_paid_subs,
                     count(DISTINCT s.account_id) AS active_paying_accounts
              FROM sub_enriched s WHERE s.is_paid AND {_ACTIVE}{w}"""
    r = _df(con, sql, [as_of, as_of] + p).iloc[0]
    return {"as_of": as_of, "mrr": float(r["mrr"]), "arr": float(r["mrr"]) * 12, "active_paid_subs": int(r["active_paid_subs"]),
            "active_paying_accounts": int(r["active_paying_accounts"])}


def mrr_definitions(con, as_of: date | None = None) -> AnalysisResult:
    """The selected MRR definition next to plausible alternatives, so users can see how much it matters."""
    as_of = as_of or snapshot_date(con)
    sql = f"""
WITH active AS (SELECT s.*, a.churn_flag AS acct_churned FROM subscriptions s JOIN accounts a USING (account_id)
               WHERE s.is_paid AND s.start_date <= ? AND (s.end_date IS NULL OR s.end_date > ?)),
one_per_account AS (SELECT account_id, arg_max(mrr_amount, start_date) AS mrr FROM active GROUP BY 1)
SELECT '1. Selected: sum of all active paid subscription rows' AS definition, sum(mrr_amount) AS mrr, count(DISTINCT account_id) AS accounts FROM active
UNION ALL SELECT '2. Alternative: one subscription per account (most recently started)', sum(mrr), count(*) FROM one_per_account
UNION ALL SELECT '3. Alternative: definition 1 excluding accounts flagged churned', sum(mrr_amount), count(DISTINCT account_id) FROM active WHERE NOT acct_churned
ORDER BY 1"""
    df = _df(con, sql, [as_of, as_of])
    df["arr (12 x MRR)"] = df["mrr"] * 12
    top, lo = df["mrr"].max(), df["mrr"].min()
    return AnalysisResult("mrr_definitions", "MRR under alternative definitions", df,
        headline=f"MRR on {as_of} ranges from {_money(lo)} to {_money(top)} depending on the definition; the selected definition (row 1) gives {_money(df.iloc[0]['mrr'])}.",
        facts=["The spread comes from the data holding many concurrent subscription rows per account and from accounts flagged churned that still hold open subscriptions (DQ-02, DQ-03)."],
        method=sql, caveats=["Recorded subscription value, not cash revenue.", "Which definition is 'right' cannot be determined from this dataset alone."],
        metric_ids=["mrr", "arr"], tables=["subscriptions", "accounts"], chart={"kind": "bar", "x": "definition", "y": "mrr"})


def current_mrr(con, f: Filters = NO_FILTERS) -> AnalysisResult:
    cur = mrr_at(con, None, f)
    prev = mrr_at(con, (pd.Timestamp(cur["as_of"]) - pd.DateOffset(months=1)).date(), f)
    mom = (cur["mrr"] / prev["mrr"] - 1) if prev["mrr"] else float("nan")
    df = pd.DataFrame([{"as_of": cur["as_of"], "mrr": cur["mrr"], "arr": cur["arr"], "active_paid_subscriptions": cur["active_paid_subs"],
                        "active_paying_accounts": cur["active_paying_accounts"], "mrr_one_month_earlier": prev["mrr"], "mom_change_pct": mom}])
    return AnalysisResult("current_mrr", "Current MRR and ARR", df,
        headline=f"MRR on {cur['as_of']} is {_money(cur['mrr'])} (ARR run-rate {_money(cur['arr'])}) across {cur['active_paying_accounts']:,} accounts "
                 f"and {cur['active_paid_subs']:,} active paid subscriptions; {_pct(mom)} vs one month earlier.",
        facts=[f"Definition: sum of mrr_amount over paid subscription rows active on the date (filters: {f.describe()})."],
        method="mrr_at(): SUM(mrr_amount) FROM sub_enriched WHERE is_paid AND start_date <= D AND (end_date IS NULL OR end_date > D)",
        caveats=["Recorded subscription value, not cash revenue; see alternative MRR definitions on the Methodology page."],
        metric_ids=["mrr", "arr", "active_paying_accounts"], tables=["subscriptions", "accounts"], stats=cur)


def monthly_series(con, f: Filters = NO_FILTERS) -> pd.DataFrame:
    start = f.start or data_start(con)
    end = f.end or snapshot_date(con)
    w, p = f.sub_where("s")
    sql = f"""
WITH sub AS (SELECT s.account_id, s.start_date, s.end_date, s.mrr_amount FROM sub_enriched s WHERE s.is_paid{w}),
bounds AS (SELECT CAST(m AS DATE) AS m, CAST(m + INTERVAL 1 MONTH - INTERVAL 1 DAY AS DATE) AS me, CAST(m - INTERVAL 1 DAY AS DATE) AS pe
           FROM generate_series(date_trunc('month', CAST(? AS DATE)), date_trunc('month', CAST(? AS DATE)), INTERVAL 1 MONTH) t(m))
SELECT b.m AS month,
  coalesce(sum(CASE WHEN s.start_date <= b.me AND (s.end_date IS NULL OR s.end_date > b.me) THEN s.mrr_amount END), 0) AS mrr,
  count(CASE WHEN s.start_date <= b.me AND (s.end_date IS NULL OR s.end_date > b.me) THEN 1 END) AS active_paid_subs,
  count(DISTINCT CASE WHEN s.start_date <= b.me AND (s.end_date IS NULL OR s.end_date > b.me) THEN s.account_id END) AS active_paying_accounts,
  count(CASE WHEN s.start_date BETWEEN b.m AND b.me THEN 1 END) AS new_subs,
  coalesce(sum(CASE WHEN s.start_date BETWEEN b.m AND b.me THEN s.mrr_amount END), 0) AS new_mrr,
  count(CASE WHEN s.start_date <= b.pe AND (s.end_date IS NULL OR s.end_date > b.pe) THEN 1 END) AS opening_subs,
  coalesce(sum(CASE WHEN s.start_date <= b.pe AND (s.end_date IS NULL OR s.end_date > b.pe) THEN s.mrr_amount END), 0) AS opening_mrr,
  count(CASE WHEN s.start_date <= b.pe AND s.end_date > b.pe AND s.end_date <= b.me THEN 1 END) AS churned_subs,
  coalesce(sum(CASE WHEN s.start_date <= b.pe AND s.end_date > b.pe AND s.end_date <= b.me THEN s.mrr_amount END), 0) AS churned_mrr,
  count(CASE WHEN s.end_date BETWEEN b.m AND b.me THEN 1 END) AS ended_subs_all
FROM bounds b LEFT JOIN sub s ON 1 = 1
GROUP BY b.m ORDER BY b.m"""
    df = _df(con, sql, p + [start, end])
    df["month"] = pd.to_datetime(df["month"])
    df["mom_mrr_pct"] = df["mrr"].pct_change().replace([np.inf, -np.inf], np.nan)
    df["sub_churn_rate"] = np.where(df["opening_subs"] > 0, df["churned_subs"] / df["opening_subs"].replace(0, np.nan), np.nan)
    df["gross_mrr_churn_rate"] = np.where(df["opening_mrr"] > 0, df["churned_mrr"] / df["opening_mrr"].replace(0, np.nan), np.nan)
    df["net_new_mrr"] = df["new_mrr"] - df["churned_mrr"]
    return df


MONTHLY_METHOD = "monthly_series(): month-end snapshots over paid subscription rows; opening-cohort churn (see Methodology)"


def mrr_trend(con, f: Filters = NO_FILTERS) -> AnalysisResult:
    s = monthly_series(con, f)
    if s.empty:
        return AnalysisResult("mrr_trend", "MRR trend", s, headline="No data for the selected filters.", metric_ids=["mrr"], tables=["subscriptions"])
    last, prev = s.iloc[-1], s.iloc[-2] if len(s) > 1 else None
    out = s[["month", "mrr", "mom_mrr_pct", "new_mrr", "churned_mrr", "net_new_mrr"]].copy()
    mom = last["mom_mrr_pct"]
    peak = s.loc[s["mrr"].idxmax()]
    neg = s[s["mom_mrr_pct"] < 0]
    facts = [f"MRR went from {_money(s.iloc[0]['mrr'])} ({s.iloc[0]['month']:%b %Y}) to {_money(last['mrr'])} ({last['month']:%b %Y}).",
             f"Peak month: {peak['month']:%b %Y} at {_money(peak['mrr'])}."]
    if len(neg):
        facts.append(f"{len(neg)} month(s) had a month-over-month MRR decline (e.g. {neg.iloc[-1]['month']:%b %Y}: {_pct(neg.iloc[-1]['mom_mrr_pct'])}).")
    return AnalysisResult("mrr_trend", "MRR trend and month-over-month change", out,
        headline=f"MRR in {last['month']:%b %Y} is {_money(last['mrr'])}, {_pct(mom)} vs {prev['month']:%b %Y}." if prev is not None else f"MRR is {_money(last['mrr'])}.",
        facts=facts, method=MONTHLY_METHOD,
        caveats=["MRR growth mostly reflects concurrent subscription rows accumulating over time (DQ-03); it should not be read as business growth without validating the definition."],
        metric_ids=["mrr", "new_subscriptions", "gross_mrr_churn_rate"], tables=["subscriptions", "accounts"],
        chart={"kind": "line", "x": "month", "y": "mrr"})


def active_subs_by_plan(con, f: Filters = NO_FILTERS) -> AnalysisResult:
    as_of = f.end or snapshot_date(con)
    w, p = f.sub_where("s")
    sql = f"""SELECT s.plan_tier AS plan, count(*) AS active_paid_subscriptions, count(DISTINCT s.account_id) AS accounts,
                     sum(s.mrr_amount) AS mrr
              FROM sub_enriched s WHERE s.is_paid AND {_ACTIVE}{w} GROUP BY 1 ORDER BY 2 DESC"""
    df = _df(con, sql, [as_of, as_of] + p)
    if df.empty:
        return AnalysisResult("active_subs_by_plan", "Active subscriptions by plan", df, headline="No active paid subscriptions for the selected filters.", metric_ids=["active_paid_subscriptions"], tables=["subscriptions"])
    df["share_of_subscriptions"] = df["active_paid_subscriptions"] / df["active_paid_subscriptions"].sum()
    top = df.iloc[0]
    return AnalysisResult("active_subs_by_plan", "Active paid subscriptions by plan", df,
        headline=f"On {as_of}, {top['plan']} has the most active paid subscriptions ({int(top['active_paid_subscriptions']):,}, {_pct(top['share_of_subscriptions'])}); "
                 + ", ".join(f"{r['plan']} {int(r['active_paid_subscriptions']):,}" for _, r in df.iloc[1:].iterrows()) + ".",
        facts=[f"Plan = subscriptions.plan_tier (billing plan). Filters: {f.describe()}."], method=sql,
        caveats=["Accounts typically hold several concurrent subscriptions, so accounts are counted separately from subscriptions."],
        metric_ids=["active_paid_subscriptions"], tables=["subscriptions", "accounts"], chart={"kind": "bar", "x": "plan", "y": "active_paid_subscriptions"})


def revenue_breakdown(con, dimension: str = "plan_tier", f: Filters = NO_FILTERS) -> AnalysisResult:
    if dimension not in REVENUE_DIMENSIONS:
        raise ValueError(f"unsupported dimension: {dimension}")
    as_of = f.end or snapshot_date(con)
    w, p = f.sub_where("s")
    sql = f"""SELECT s.{dimension} AS {dimension}, sum(s.mrr_amount) AS mrr, count(*) AS active_paid_subscriptions,
                     count(DISTINCT s.account_id) AS accounts
              FROM sub_enriched s WHERE s.is_paid AND {_ACTIVE}{w} GROUP BY 1 ORDER BY 2 DESC"""
    df = _df(con, sql, [as_of, as_of] + p)
    label = REVENUE_DIMENSIONS[dimension]
    if df.empty:
        return AnalysisResult("revenue_breakdown", f"MRR by {label}", df, headline="No data for the selected filters.", metric_ids=["mrr_breakdown"], tables=["subscriptions"])
    df["share_of_mrr"] = df["mrr"] / df["mrr"].sum()
    top = df.iloc[0]
    return AnalysisResult("revenue_breakdown", f"MRR by {label}", df,
        headline=f"{top[dimension]} contributes the most MRR by {label}: {_money(top['mrr'])} ({_pct(top['share_of_mrr'])} of {_money(df['mrr'].sum())}) on {as_of}.",
        facts=[f"{r[dimension]}: {_money(r['mrr'])} ({_pct(r['share_of_mrr'])})" for _, r in df.head(5).iterrows()], method=sql,
        caveats=["Recorded subscription value, not cash revenue; shares depend on the MRR definition (DQ-03)."],
        metric_ids=["mrr_breakdown", "mrr"], tables=["subscriptions", "accounts"], chart={"kind": "bar", "x": dimension, "y": "mrr"})


# ------------------------------------------------------------------------------------------------------
# Churn
# ------------------------------------------------------------------------------------------------------
def churn_by_segment(con, dimension: str = "industry", f: Filters = NO_FILTERS, basis: str = "flag") -> AnalysisResult:
    if dimension not in ACCOUNT_DIMENSIONS:
        raise ValueError(f"unsupported dimension: {dimension}")
    if basis not in ("flag", "event"):
        raise ValueError("basis must be 'flag' or 'event'")
    w, p = f.acct_where("a")
    flag = "a.churn_flag" if basis == "flag" else "a.has_churn_event"
    sql = f"""SELECT a.{dimension} AS {dimension}, count(*) AS accounts, sum({flag}::INT) AS churned
              FROM account_features a WHERE 1 = 1{w} GROUP BY 1 ORDER BY 1"""
    df = _df(con, sql, p)
    label = ACCOUNT_DIMENSIONS[dimension]
    basis_txt = "churn_flag" if basis == "flag" else "any churn event"
    if df.empty:
        return AnalysisResult("churn_by_segment", f"Churn by {label}", df, headline="No accounts match the selected filters.", metric_ids=["account_churn_rate_flag"], tables=["accounts"])
    if dimension == "seats_band":
        order = {"1-5": 0, "6-20": 1, "21-50": 2, "51+": 3}
        df = df.sort_values(dimension, key=lambda s: s.map(order))
    df["churn_rate"] = df["churned"] / df["accounts"]
    ci = [wilson_interval(int(k), int(n)) for k, n in zip(df["churned"], df["accounts"])]
    df["ci_low"], df["ci_high"] = [c[0] for c in ci], [c[1] for c in ci]
    st = chi_square_churn(df, dimension, "churned", "accounts")
    overall = df["churned"].sum() / df["accounts"].sum()
    ranked = df.sort_values("churn_rate", ascending=False)
    hi, lo = ranked.iloc[0], ranked.iloc[-1]
    if dimension != "seats_band":
        df = df.sort_values("churn_rate", ascending=False).reset_index(drop=True)
    facts = [f"Overall observed churn: {_pct(overall)} ({int(df['churned'].sum()):,} of {int(df['accounts'].sum()):,} accounts, basis: {basis_txt}).",
             f"Differences across {label}: {describe_p(st['p_value'])}."]
    if st["small_expected"]:
        facts.append("Some segments are small (expected counts below 5), so the test is unreliable.")
    thin = ranked[ranked["accounts"] < 30]
    if len(thin):
        facts.append("Segments with fewer than 30 accounts are very uncertain: " + ", ".join(map(str, thin[dimension])) + ".")
    caveats = ["Observed rates are cumulative since signup (older accounts had longer to churn) and are associations, not causes.",
               "churn_flag conflicts with churn events for 62% of accounts (DQ-01); event-based rates are much higher."]
    if dimension == "plan_tier":
        caveats.append("Uses accounts.plan_tier, which disagrees with the subscription plan for many accounts (DQ-07).")
    return AnalysisResult("churn_by_segment", f"Observed churn rate by {label}", df,
        headline=f"Highest observed churn by {label}: {hi[dimension]} at {_pct(hi['churn_rate'])} (n={int(hi['accounts'])}) vs lowest {lo[dimension]} at {_pct(lo['churn_rate'])} (n={int(lo['accounts'])}); "
                 f"overall {_pct(overall)}. The difference is {describe_p(st['p_value'])}.",
        facts=facts, method=sql, caveats=caveats, metric_ids=["account_churn_rate_flag" if basis == "flag" else "account_churn_rate_event"],
        tables=["accounts", "churn_events"], chart={"kind": "bar", "x": dimension, "y": "churn_rate"}, stats=st)


def churn_reasons(con, f: Filters = NO_FILTERS) -> AnalysisResult:
    w, p = f.acct_where("a")
    sql = f"""SELECT e.reason_code AS reason, count(*) AS churn_events, count(DISTINCT e.account_id) AS accounts
              FROM churn_events e JOIN account_features a USING (account_id)
              WHERE NOT e.dq_duplicate_account_date{w} GROUP BY 1 ORDER BY 2 DESC"""
    df = _df(con, sql, p)
    if df.empty:
        return AnalysisResult("churn_reasons", "Recorded churn reasons", df, headline="No churn events for the selected filters.", metric_ids=["churn_reasons"], tables=["churn_events"])
    df["share"] = df["churn_events"] / df["churn_events"].sum()
    top = df.iloc[0]
    known = df[df["reason"] != "unknown"]
    facts = [f"{r['reason']}: {int(r['churn_events'])} events ({_pct(r['share'])})" for _, r in df.iterrows()]
    spread = (df["share"].max() - df["share"].min())
    return AnalysisResult("churn_reasons", "Recorded churn reasons", df,
        headline=f"The most common recorded churn reason is '{top['reason']}' ({int(top['churn_events'])} of {int(df['churn_events'].sum())} events, {_pct(top['share'])}); "
                 f"reasons are spread fairly evenly (top-to-bottom gap {_pct(spread)} points).",
        facts=facts, method=sql,
        caveats=["reason_code disagrees with free-text feedback about as often as chance (DQ-11): treat reasons as recorded labels, not causes.",
                 "Counts churn events (one account can have several), not accounts."],
        metric_ids=["churn_reasons"], tables=["churn_events", "accounts"], chart={"kind": "bar", "x": "reason", "y": "churn_events"})


def monthly_churn(con, f: Filters = NO_FILTERS) -> AnalysisResult:
    s = monthly_series(con, f)
    out = s[["month", "opening_subs", "churned_subs", "sub_churn_rate", "opening_mrr", "churned_mrr", "gross_mrr_churn_rate"]].copy()
    valid = out[out["opening_subs"] >= 30]
    if valid.empty:
        return AnalysisResult("monthly_churn", "Monthly churn", out, headline="Not enough opening subscriptions to compute a stable churn rate.",
                              metric_ids=["subscription_churn_rate"], tables=["subscriptions"])
    last = valid.iloc[-1]
    return AnalysisResult("monthly_churn", "Monthly subscription and gross MRR churn", out,
        headline=f"In {last['month']:%b %Y}, {_pct(last['sub_churn_rate'], 2)} of opening paid subscriptions ended ({int(last['churned_subs'])} of {int(last['opening_subs'])}) "
                 f"and gross MRR churn was {_pct(last['gross_mrr_churn_rate'], 2)}.",
        facts=[f"Months with fewer than 30 opening subscriptions are excluded from the headline (unstable rates)."],
        method=MONTHLY_METHOD, caveats=["Churn spikes in the final quarter of the data (DQ-16); do not read as a trend without more history.", "Gross churn only: expansion/contraction not measurable."],
        metric_ids=["subscription_churn_rate", "gross_mrr_churn_rate"], tables=["subscriptions"], chart={"kind": "line", "x": "month", "y": "gross_mrr_churn_rate"})


def cohort_retention(con, f: Filters = NO_FILTERS, max_months: int = 18) -> AnalysisResult:
    w, p = f.acct_where("a")
    df = _df(con, f"SELECT a.account_id, a.signup_date, a.signup_quarter, a.first_churn_date FROM account_features a WHERE 1 = 1{w}", p)
    snap = pd.Timestamp(snapshot_date(con))
    rows = []
    if not df.empty:
        df["signup_date"] = pd.to_datetime(df["signup_date"])
        df["first_churn_date"] = pd.to_datetime(df["first_churn_date"])
        for cohort, g in df.groupby("signup_quarter"):
            cohort_end = pd.Timestamp(cohort) + pd.offsets.QuarterEnd(0)
            for k in range(1, max_months + 1):
                if cohort_end + pd.DateOffset(months=k) > snap:
                    break
                limit = g["signup_date"].apply(lambda d: d + pd.DateOffset(months=k))
                churned = ((g["first_churn_date"].notna()) & (g["first_churn_date"] <= limit)).sum()
                rows.append({"cohort": f"{pd.Timestamp(cohort):%Y}-Q{(pd.Timestamp(cohort).month - 1) // 3 + 1}", "months_since_signup": k,
                             "accounts": len(g), "retained": len(g) - int(churned), "retention": 1 - churned / len(g)})
    out = pd.DataFrame(rows, columns=["cohort", "months_since_signup", "accounts", "retained", "retention"])
    if out.empty:
        return AnalysisResult("cohort_retention", "Cohort retention", out, headline="No fully observable cohorts for the selected filters.", metric_ids=["cohort_retention"], tables=["churn_events"])
    available = set(out["months_since_signup"])
    probe = next((k for k in (12, 6, 3) if k in available), max(available))
    at = out[out["months_since_signup"] == probe].sort_values("retention", ascending=False)
    best, worst = at.iloc[0], at.iloc[-1]
    return AnalysisResult("cohort_retention", "Cohort retention (no churn event since signup)", out,
        headline=f"At month {probe}, the strongest cohort is {best['cohort']} ({_pct(best['retention'])} with no churn event, n={int(best['accounts'])}) and the weakest is "
                 f"{worst['cohort']} ({_pct(worst['retention'])}, n={int(worst['accounts'])}).",
        facts=[f"Cohort = signup quarter; retained = no churn event within k months of the account's own signup date; only fully observable cohort/month cells are shown.",
               "Cohorts have 50-90 accounts, so differences of a few points are within noise.",
               "Possible explanation (not tested): cells whose window ends in Q4 2024 look worse because churn events are concentrated there for every cohort (DQ-16), so the gap may reflect calendar timing rather than cohort quality."],
        method="cohort_retention(): per signup-quarter cohort, share with no non-duplicate churn_event within k months of signup_date",
        caveats=["Event-based definition conflicts with churn_flag (DQ-01).", "Churn events cluster in late 2024 for every cohort, so cohort differences are confounded by calendar time (DQ-16)."],
        metric_ids=["cohort_retention"], tables=["accounts", "churn_events"], chart={"kind": "heatmap", "x": "months_since_signup", "y": "cohort", "z": "retention"})


# ------------------------------------------------------------------------------------------------------
# Support & usage
# ------------------------------------------------------------------------------------------------------
def _association(con, metric: str, labels: dict, f: Filters, rid: str, title: str, metric_ids: list, tables: list, extra_caveats: list) -> AnalysisResult:
    if metric not in labels:
        raise ValueError(f"unsupported metric: {metric}")
    w, p = f.acct_where("a")
    df = _df(con, f"SELECT a.account_id, a.churn_flag, a.{metric} AS value FROM account_features a WHERE a.{metric} IS NOT NULL{w}", p)
    total = _df(con, f"SELECT count(*) AS n FROM account_features a WHERE 1 = 1{w}", p).iloc[0, 0]
    label = labels[metric]
    if len(df) < 20:
        return AnalysisResult(rid, title, df.head(0), headline=f"Too few accounts ({len(df)}) with data on {label} to analyse.",
                              method=f"account_features: churn_flag vs quartile bands of {metric} (needs at least 20 accounts with data)", metric_ids=metric_ids, tables=tables)
    try:
        df["band"] = pd.qcut(df["value"], 4, duplicates="drop")
    except ValueError:
        df["band"] = pd.cut(df["value"], 2)
    g = df.groupby("band", observed=True).agg(accounts=("churn_flag", "size"), churned=("churn_flag", "sum"), median_value=("value", "median")).reset_index()
    g["band"] = g["band"].map(lambda iv: f"{iv.left:.4g} to {iv.right:.4g}" if hasattr(iv, "left") else str(iv))
    g["churn_rate"] = g["churned"] / g["accounts"]
    ci = [wilson_interval(int(k), int(n)) for k, n in zip(g["churned"], g["accounts"])]
    g["ci_low"], g["ci_high"] = [c[0] for c in ci], [c[1] for c in ci]
    mw = mann_whitney(df.loc[df["churn_flag"], "value"], df.loc[~df["churn_flag"], "value"])
    med_c, med_n = df.loc[df["churn_flag"], "value"].median(), df.loc[~df["churn_flag"], "value"].median()
    excluded = int(total - len(df))
    facts = [f"Median {label}: {med_c:,.1f} for churn-flagged accounts vs {med_n:,.1f} for others.",
             f"Comparison of the two groups: {describe_p(mw['p_value'])} (Mann-Whitney U, n={mw['n_a']} churned vs {mw['n_b']} not)."]
    if excluded:
        facts.append(f"{excluded} accounts have no {label} data and are excluded.")
    return AnalysisResult(rid, title, g,
        headline=f"Churn rate by {label} band ranges from {_pct(g['churn_rate'].min())} to {_pct(g['churn_rate'].max())}; "
                 f"difference between churned and retained accounts is {describe_p(mw['p_value'])}.",
        facts=facts, method=f"account_features: churn_flag vs quartile bands of {metric} (Mann-Whitney U on raw values)",
        caveats=["Association only; the data cannot show that this factor causes churn.", "churn_flag has no timestamp, so we cannot tell whether the factor came before churn."] + extra_caveats,
        metric_ids=metric_ids, tables=tables, chart={"kind": "bar", "x": "band", "y": "churn_rate"}, stats=mw)


def support_vs_churn(con, metric: str = "avg_first_response_min", f: Filters = NO_FILTERS) -> AnalysisResult:
    return _association(con, metric, SUPPORT_METRICS, f, "support_vs_churn", f"Churn vs {SUPPORT_METRICS.get(metric, metric)}",
                        ["support_metrics", "account_churn_rate_flag"], ["support_tickets", "accounts"],
                        ["54% of tickets predate account signup (DQ-08); CSAT never below 3 (DQ-10)."])


def usage_vs_churn(con, metric: str = "total_usage_count", f: Filters = NO_FILTERS) -> AnalysisResult:
    return _association(con, metric, USAGE_METRICS, f, "usage_vs_churn", f"Churn vs {USAGE_METRICS.get(metric, metric)}",
                        ["feature_engagement", "account_churn_rate_flag"], ["feature_usage", "accounts"],
                        ["Usage is a lifetime aggregate; 78% of usage rows fall outside their subscription window (DQ-04), so pre-churn usage trends cannot be assessed."])


def support_summary(con, f: Filters = NO_FILTERS) -> AnalysisResult:
    w, p = f.acct_where("a")
    sql = f"""SELECT t.priority, count(*) AS tickets, round(avg(t.first_response_time_minutes), 1) AS avg_first_response_min,
                     round(avg(t.resolution_time_hours), 1) AS avg_resolution_hours, round(avg(t.satisfaction_score), 2) AS avg_csat,
                     count(t.satisfaction_score) AS csat_responses, round(avg(t.escalation_flag::INT), 3) AS escalation_rate
              FROM support_tickets t JOIN account_features a USING (account_id) WHERE 1 = 1{w} GROUP BY 1
              ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END"""
    df = _df(con, sql, p)
    if df.empty:
        return AnalysisResult("support_summary", "Support summary", df, headline="No tickets for the selected filters.", metric_ids=["support_metrics"], tables=["support_tickets"])
    tot = int(df["tickets"].sum())
    w_resp = (df["avg_first_response_min"] * df["tickets"]).sum() / tot
    w_res = (df["avg_resolution_hours"] * df["tickets"]).sum() / tot
    return AnalysisResult("support_summary", "Support performance by priority", df,
        headline=f"{tot:,} tickets: average first response {w_resp:.0f} minutes and resolution {w_res:.1f} hours; "
                 f"urgent tickets take {df.iloc[0]['avg_resolution_hours']} h vs {df.iloc[-1]['avg_resolution_hours']} h for low priority.",
        facts=["If priority were respected, urgent tickets should resolve faster; compare rows to judge."],
        method=sql, caveats=["CSAT is based on responded tickets only (DQ-10)."], metric_ids=["support_metrics"], tables=["support_tickets", "accounts"],
        chart={"kind": "bar", "x": "priority", "y": "avg_resolution_hours"})


def feature_usage_summary(con, f: Filters = NO_FILTERS, top_n: int = 15) -> AnalysisResult:
    w, p = f.sub_where("s")
    sql = f"""SELECT u.feature_name AS feature, count(*) AS usage_records, sum(u.usage_count) AS total_usage_count,
                     count(DISTINCT s.account_id) AS accounts_using, round(sum(u.error_count) * 1.0 / nullif(sum(u.usage_count), 0), 3) AS errors_per_use
              FROM feature_usage u JOIN sub_enriched s USING (subscription_id) WHERE 1 = 1{w}
              GROUP BY 1 ORDER BY total_usage_count DESC LIMIT {int(top_n)}"""
    df = _df(con, sql, p)
    if df.empty:
        return AnalysisResult("feature_usage_summary", "Feature usage", df, headline="No usage for the selected filters.", metric_ids=["feature_engagement"], tables=["feature_usage"])
    top = df.iloc[0]
    return AnalysisResult("feature_usage_summary", f"Top {len(df)} features by usage", df,
        headline=f"The most-used feature is {top['feature']} ({int(top['total_usage_count']):,} uses across {int(top['accounts_using'])} accounts).",
        facts=["Usage records are joined to subscriptions 1:1, so counts are not duplicated."], method=sql,
        caveats=["Lifetime usage; 78% of usage rows are dated outside their subscription window (DQ-04)."],
        metric_ids=["feature_engagement"], tables=["feature_usage", "subscriptions"], chart={"kind": "bar", "x": "feature", "y": "total_usage_count"})


# ------------------------------------------------------------------------------------------------------
# Anomalies and data gaps
# ------------------------------------------------------------------------------------------------------
def flag_spikes(s: pd.Series, window: int = 3, ratio: float = 2.0, min_value: float = 10) -> pd.DataFrame:
    """Flag points that are at least `ratio` times the mean of the previous `window` points (and >= min_value)."""
    rows = []
    vals = s.reset_index(drop=True)
    for i in range(window, len(vals)):
        base = vals.iloc[i - window:i].mean()
        if vals.iloc[i] >= min_value and base > 0 and vals.iloc[i] >= ratio * base:
            rows.append({"period": s.index[i], "value": float(vals.iloc[i]), "trailing_avg": float(base), "ratio": float(vals.iloc[i] / base)})
    return pd.DataFrame(rows, columns=["period", "value", "trailing_avg", "ratio"])


def unusual_changes(con, f: Filters = NO_FILTERS) -> AnalysisResult:
    s = monthly_series(con, f).set_index("month")
    ev = _df(con, "SELECT churn_month AS month, count(*) AS n FROM churn_events WHERE NOT dq_duplicate_account_date GROUP BY 1 ORDER BY 1")
    ev["month"] = pd.to_datetime(ev["month"])
    ev = ev.set_index("month")["n"]
    findings = []
    for name, series in (("subscriptions ended", s["ended_subs_all"]), ("new paid subscriptions", s["new_subs"]), ("churn events", ev)):
        for _, r in flag_spikes(series, min_value=15).iterrows():
            findings.append({"metric": name, "month": pd.Timestamp(r["period"]).strftime("%Y-%m"), "value": r["value"], "trailing_3m_avg": round(r["trailing_avg"], 1), "x_trailing_avg": round(r["ratio"], 1)})
    neg = s[s["mom_mrr_pct"] < 0]
    for m, r in neg.iterrows():
        findings.append({"metric": "MRR month-over-month decline", "month": m.strftime("%Y-%m"), "value": r["mom_mrr_pct"], "trailing_3m_avg": None, "x_trailing_avg": None})
    df = pd.DataFrame(findings, columns=["metric", "month", "value", "trailing_3m_avg", "x_trailing_avg"])
    tot_end = s["ended_subs_all"].sum()
    last_q = s["ended_subs_all"].tail(3).sum()
    facts = []
    if tot_end:
        facts.append(f"{int(last_q)} of {int(tot_end)} subscription endings ({_pct(last_q / tot_end)}) fall in the final three months of data.")
    if len(ev) >= 3:
        facts.append(f"Churn events in the last 3 months: {int(ev.tail(3).sum())} of {int(ev.sum())} ({_pct(ev.tail(3).sum() / ev.sum())}).")
    if len(df):
        cutoff = (s.index.max() - pd.DateOffset(months=2)).strftime("%Y-%m")
        recent = int((df["month"] >= cutoff).sum())
        head = f"{len(df)} unusual movement(s) flagged; {recent} of them fall in the final three months of the data."
    else:
        head = "No spikes beyond the detection thresholds were found."
    return AnalysisResult("unusual_changes", "Unusual changes in the data", df, headline=head, facts=facts,
        method="flag_spikes(): month value >= 2x the mean of the preceding 3 months (minimum 15 for counts); negative MoM MRR months listed",
        caveats=["Spikes may be artifacts of how the synthetic data was generated (DQ-16); they are observations, not diagnoses.",
                 "Detection thresholds are heuristic."],
        metric_ids=["subscription_churn_rate", "mrr"], tables=["subscriptions", "churn_events"])


def data_gaps(con=None, f: Filters = NO_FILTERS) -> AnalysisResult:
    """What additional data would help; derived from the metric registry (not hand-written per question)."""
    needs = {"cash_revenue": "Invoices/payments (amounts, dates, failures)", "customer_lifetime_value": "Payments + margin + a single trusted churn record",
             "net_revenue_retention": "Plan/seat change history linked to MRR", "cac": "Marketing and sales spend by channel",
             "trial_conversion": "Consistent trial start/convert events", "feature_engagement": "Time-aligned user-level usage events",
             "gross_mrr_churn_rate": "Contraction/expansion events", "account_churn_rate_flag": "A single churn date/status of record per account"}
    rows = [{"metric": m.name, "status": m.status, "what_is_missing": needs.get(m.id, m.limitations)} for m in unavailable_metrics()]
    df = pd.DataFrame(rows)
    return AnalysisResult("data_gaps", "Metrics that are unavailable or approximate, and the data that would fix them", df,
        headline=f"{(df['status'] == 'unavailable').sum()} metrics are unavailable and {(df['status'] == 'approximate').sum()} are approximate with the supplied data.",
        facts=["Highest-value additions: payments/invoices (cash revenue), a single churn record of truth with dates, time-aligned user-level usage, and spend by channel."],
        method="metric registry (src/metrics/definitions.py)", caveats=[], metric_ids=[], tables=[])


# ------------------------------------------------------------------------------------------------------
# Dashboard KPIs
# ------------------------------------------------------------------------------------------------------
def kpis(con, f: Filters = NO_FILTERS) -> dict:
    cur = mrr_at(con, None, f)
    prev = mrr_at(con, (pd.Timestamp(cur["as_of"]) - pd.DateOffset(months=1)).date(), f)
    w, p = f.acct_where("a")
    a = _df(con, f"SELECT count(*) AS accounts, coalesce(sum(a.churn_flag::INT), 0) AS churned, coalesce(sum(a.has_churn_event::INT), 0) AS with_events FROM account_features a WHERE 1 = 1{w}", p).iloc[0]
    return {**cur, "mom_pct": (cur["mrr"] / prev["mrr"] - 1) if prev["mrr"] else None, "accounts": int(a["accounts"]),
            "churned_accounts": int(a["churned"]), "account_churn_rate": (a["churned"] / a["accounts"]) if a["accounts"] else None,
            "accounts_with_churn_event": int(a["with_events"])}


def high_value_at_risk(con, f: Filters = NO_FILTERS, top_n: int = 20) -> AnalysisResult:
    """Transparent rule-based watchlist (NOT a predictive model): top-quartile MRR accounts with a churn event in the last 90 days."""
    snap = snapshot_date(con)
    w, p = f.acct_where("a")
    sql = f"""WITH last_evt AS (SELECT account_id, max(churn_date) AS last_churn_event, count(*) AS churn_events
                              FROM churn_events WHERE NOT dq_duplicate_account_date GROUP BY 1),
              cut AS (SELECT quantile_cont(mrr_at_snapshot, 0.75) AS q75 FROM account_features)
              SELECT a.account_id, a.account_name, a.industry, a.country, a.plan_tier AS account_plan, a.mrr_at_snapshot AS mrr,
                     a.churn_flag, l.last_churn_event, l.churn_events, a.n_escalations
              FROM account_features a JOIN last_evt l USING (account_id), cut
              WHERE a.mrr_at_snapshot >= cut.q75 AND l.last_churn_event >= CAST(? AS DATE) - INTERVAL 90 DAY{w}
              ORDER BY a.mrr_at_snapshot DESC LIMIT {int(top_n)}"""
    df = _df(con, sql, [snap] + p)
    total_mrr = float(_df(con, "SELECT sum(mrr_at_snapshot) AS m FROM account_features").iloc[0, 0] or 0)
    if df.empty:
        return AnalysisResult("high_value_at_risk", "High-value accounts with recent churn events", df, headline="No accounts match the watchlist rule.",
                              method=sql, metric_ids=["mrr"], tables=["accounts", "churn_events"])
    return AnalysisResult("high_value_at_risk", "High-value accounts with recent churn events (rule-based watchlist)", df,
        headline=f"{len(df)} top-quartile-MRR accounts had a churn event in the 90 days to {snap}; together they hold {_money(df['mrr'].sum())} of recorded MRR "
                 f"({_pct(df['mrr'].sum() / total_mrr)} of the total). {int(df['churn_flag'].sum())} of them are also flagged churned.",
        facts=["Rule: account MRR in the top quartile AND a non-duplicate churn event within 90 days of the snapshot date. This is a transparent filter, not a predicted probability."],
        method=sql, caveats=["Churn events do not reliably mean the account left (DQ-01: 277 accounts have events but no churn flag).",
                             "No validated predictors of churn were found in this data, so no risk score is offered."],
        metric_ids=["mrr", "account_churn_rate_event"], tables=["accounts", "churn_events", "subscriptions"])
