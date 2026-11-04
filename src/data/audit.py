"""Data audit: profiling + data-quality issue detection directly on the raw (typed) CSVs.

Every issue records severity, affected rows, impact on analysis and the handling chosen, plus the metrics
and tables it can affect (used by the app to show relevant warnings next to results).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import duckdb

from .loader import register_raw_views
from .schema import FOREIGN_KEYS, TABLES

SEVERITIES = ["High", "Medium", "Low", "Info"]


@dataclass
class Issue:
    id: str
    title: str
    severity: str
    table: str
    affected: int
    total: int
    description: str
    impact: str
    handling: str
    affects_metrics: list = field(default_factory=list)
    affects_tables: list = field(default_factory=list)

    @property
    def pct(self) -> float:
        return round(100.0 * self.affected / self.total, 1) if self.total else 0.0


def _one(con, sql: str):
    return con.execute(sql).fetchone()[0]


def profile_tables(con: duckdb.DuckDBPyConnection) -> list[dict]:
    out = []
    for name, spec in TABLES.items():
        cols = []
        n = _one(con, f"SELECT count(*) FROM raw_{name}")
        for c, t in spec.columns.items():
            nulls = _one(con, f'SELECT count(*) FROM raw_{name} WHERE "{c}" IS NULL')
            distinct = _one(con, f'SELECT count(DISTINCT "{c}") FROM raw_{name}')
            lo = hi = None
            if t in ("DATE", "TIMESTAMP", "INTEGER", "DOUBLE"):
                lo, hi = con.execute(f'SELECT min("{c}"), max("{c}") FROM raw_{name}').fetchone()
            cols.append({"column": c, "type": t, "nulls": nulls, "null_pct": round(100 * nulls / n, 1) if n else 0,
                         "distinct": distinct, "min": None if lo is None else str(lo), "max": None if hi is None else str(hi)})
        out.append({"table": name, "file": spec.filename, "rows": n, "columns": len(spec.columns), "profile": cols})
    return out


def run_checks(con: duckdb.DuckDBPyConnection) -> tuple[list[Issue], list[dict]]:
    """Returns (issues, passed_checks)."""
    issues: list[Issue] = []
    passed: list[dict] = []
    n_acc = _one(con, "SELECT count(*) FROM raw_accounts")
    n_sub = _one(con, "SELECT count(*) FROM raw_subscriptions")
    n_use = _one(con, "SELECT count(*) FROM raw_feature_usage")
    n_tix = _one(con, "SELECT count(*) FROM raw_support_tickets")
    n_evt = _one(con, "SELECT count(*) FROM raw_churn_events")

    def check(desc: str, bad: int):
        if bad == 0:
            passed.append({"check": desc, "result": "passed"})
        return bad

    # --- structural checks -------------------------------------------------------------------------
    for name, spec in TABLES.items():
        dup = _one(con, f"SELECT count(*) - count(DISTINCT {spec.pk}) FROM raw_{name}")
        if check(f"{name}.{spec.pk} is unique", dup) and name != "feature_usage":
            issues.append(Issue(f"DQ-PK-{name}", f"Duplicate primary keys in {name}", "High", name, dup,
                                _one(con, f"SELECT count(*) FROM raw_{name}"), f"{dup} duplicate {spec.pk} values.",
                                "Joins may double count.", "Investigate; deduplicate with documentation.", [], [name]))
    for child, ccol, parent, pcol in FOREIGN_KEYS:
        orphans = _one(con, f"SELECT count(*) FROM raw_{child} c WHERE c.{ccol} NOT IN (SELECT {pcol} FROM raw_{parent})")
        if check(f"{child}.{ccol} -> {parent}.{pcol} has no orphans", orphans):
            issues.append(Issue(f"DQ-FK-{child}", f"Orphan records in {child}", "High", child, orphans,
                                _one(con, f"SELECT count(*) FROM raw_{child}"), "Foreign key without parent row.",
                                "Rows silently dropped by inner joins.", "Flag and exclude from joined metrics.", [], [child]))
    check("subscriptions.end_date >= start_date", _one(con, "SELECT count(*) FROM raw_subscriptions WHERE end_date < start_date"))
    check("subscriptions.arr_amount = 12 x mrr_amount", _one(con, "SELECT count(*) FROM raw_subscriptions WHERE arr_amount <> 12*mrr_amount"))
    check("subscriptions.seats > 0", _one(con, "SELECT count(*) FROM raw_subscriptions WHERE seats <= 0"))
    check("trial subscriptions have MRR = 0 and paid have MRR > 0", _one(con, "SELECT count(*) FROM raw_subscriptions WHERE (is_trial AND mrr_amount<>0) OR (NOT is_trial AND mrr_amount<=0)"))
    check("subscription churn_flag matches end_date presence", _one(con, "SELECT count(*) FROM raw_subscriptions WHERE churn_flag <> (end_date IS NOT NULL)"))
    check("subscriptions never start before account signup", _one(con, "SELECT count(*) FROM raw_subscriptions s JOIN raw_accounts a USING(account_id) WHERE s.start_date < a.signup_date"))
    check("churn events never precede account signup", _one(con, "SELECT count(*) FROM raw_churn_events e JOIN raw_accounts a USING(account_id) WHERE e.churn_date < a.signup_date"))
    check("tickets close after they open", _one(con, "SELECT count(*) FROM raw_support_tickets WHERE closed_at < submitted_at"))

    # --- 1. conflicting churn indicators --------------------------------------------------------------
    conflict = _one(con, """SELECT count(*) FROM raw_accounts a WHERE a.churn_flag <>
                  (EXISTS (SELECT 1 FROM raw_churn_events e WHERE e.account_id = a.account_id))""")
    flag_no_evt = _one(con, """SELECT count(*) FROM raw_accounts a WHERE a.churn_flag AND NOT
                  EXISTS (SELECT 1 FROM raw_churn_events e WHERE e.account_id = a.account_id)""")
    evt_no_flag = conflict - flag_no_evt
    issues.append(Issue("DQ-01", "Account churn_flag conflicts with churn_events", "High", "accounts", conflict, n_acc,
        f"{flag_no_evt} accounts are flagged churned but have no churn event; {evt_no_flag} accounts have churn events but are not flagged.",
        "Churn rate depends on definition: flag-based vs event-based results differ massively. Timing-based analysis (cohorts, trends) cannot use the flag.",
        "Primary account churn label = accounts.churn_flag (documented). Events used for timing, reasons and cohorts. Both are exposed; conflicts flagged in account_features.dq_churn_flag_conflict.",
        ["account_churn_rate_flag", "account_churn_rate_event", "cohort_retention", "churn_reasons"], ["accounts", "churn_events"]))

    # --- 2. churned accounts still hold open paid subscriptions ------------------------------------------
    n_churn_open, mrr_churn_open = con.execute("""SELECT count(*), coalesce(sum(m),0) FROM (SELECT a.account_id, sum(s.mrr_amount) m
        FROM raw_accounts a JOIN raw_subscriptions s USING(account_id) WHERE a.churn_flag AND s.end_date IS NULL AND NOT s.is_trial GROUP BY 1)""").fetchone()
    n_flagged = _one(con, "SELECT count(*) FROM raw_accounts WHERE churn_flag")
    issues.append(Issue("DQ-02", "Accounts flagged as churned still hold open paid subscriptions", "High", "accounts/subscriptions",
        n_churn_open, n_flagged, f"{n_churn_open} of {n_flagged} churned-flag accounts have open (end_date NULL) paid subscriptions worth ${mrr_churn_open:,.0f} recorded MRR.",
        "Snapshot MRR includes revenue from accounts the data itself calls churned; MRR may be overstated or the churn flag may mean 'churned at some point' (it may have reactivated).",
        "MRR is reported 'as recorded' from subscriptions; MRR from churn-flagged accounts is shown as a sensitivity on the Methodology page.",
        ["mrr", "arr", "active_paying_accounts"], ["accounts", "subscriptions"]))

    # --- 3. concurrent subscription line items ----------------------------------------------------------
    avg_open = _one(con, """SELECT avg(c) FROM (SELECT account_id, count(*) c FROM raw_subscriptions
                           WHERE end_date IS NULL AND NOT is_trial GROUP BY 1)""")
    multi = _one(con, """SELECT count(*) FROM (SELECT account_id FROM raw_subscriptions WHERE end_date IS NULL AND NOT is_trial
                         GROUP BY 1 HAVING count(*) > 1)""")
    issues.append(Issue("DQ-03", "Many concurrent open subscriptions per account (overlapping periods)", "High", "subscriptions", multi, n_acc,
        f"{multi} accounts hold more than one open paid subscription at the same time (avg {avg_open:.1f} per account; accounts average {n_sub / n_acc:.0f} subscription rows).",
        "Summing active subscription rows gives very high MRR (average per account is far above the plan price card). If rows are billing periods rather than concurrent contracts, MRR is overstated several-fold.",
        "Selected definition: MRR = sum of mrr_amount over paid subscriptions open on the date (treat rows as concurrent line items). Alternative definitions are computed side-by-side and shown to users.",
        ["mrr", "arr", "active_paid_subscriptions", "new_subscriptions", "gross_mrr_churn_rate", "subscription_churn_rate"], ["subscriptions"]))

    # --- 4. temporal misalignment of usage ----------------------------------------------------------------
    before = _one(con, "SELECT count(*) FROM raw_feature_usage u JOIN raw_subscriptions s USING(subscription_id) WHERE u.usage_date < s.start_date")
    after = _one(con, "SELECT count(*) FROM raw_feature_usage u JOIN raw_subscriptions s USING(subscription_id) WHERE s.end_date IS NOT NULL AND u.usage_date > s.end_date")
    issues.append(Issue("DQ-04", "Feature usage dated outside its subscription window", "High", "feature_usage", before + after, n_use,
        f"{before} usage rows predate the subscription start and {after} postdate its end.",
        "Usage is not lifecycle-aligned: onboarding/activation, pre-churn usage decline and time-based engagement cannot be measured validly.",
        "Usage is used only as lifetime per-account aggregates (total events, distinct features); no time-aligned engagement claims.",
        ["feature_engagement"], ["feature_usage"]))
    dup_ids = _one(con, "SELECT count(*) FROM raw_feature_usage WHERE usage_id IN (SELECT usage_id FROM raw_feature_usage GROUP BY 1 HAVING count(*) > 1)")
    issues.append(Issue("DQ-05", "usage_id reused by different records", "Medium", "feature_usage", dup_ids, n_use,
        f"{dup_ids} rows share a usage_id with another row (distinct records, not exact duplicates).",
        "usage_id is not a valid primary key; counting distinct usage_id undercounts events.",
        "Surrogate key usage_row_id added; all rows kept; usage_id retained for traceability only.", ["feature_engagement"], ["feature_usage"]))
    dup_combo = _one(con, "SELECT coalesce(sum(c),0) FROM (SELECT count(*) c FROM raw_feature_usage GROUP BY subscription_id, usage_date, feature_name HAVING count(*) > 1)")
    issues.append(Issue("DQ-06", "Repeated subscription/date/feature usage rows", "Low", "feature_usage", dup_combo, n_use,
        f"{dup_combo} rows repeat a subscription+date+feature combination.", "Minor; may be legitimate separate sessions.",
        "Kept; no deduplication applied.", ["feature_engagement"], ["feature_usage"]))

    # --- 5. plan tier inconsistency ------------------------------------------------------------------------
    mism = _one(con, """SELECT count(*) FROM raw_accounts a JOIN (SELECT account_id, arg_min(plan_tier, start_date) p FROM raw_subscriptions GROUP BY 1) f
                        USING(account_id) WHERE a.plan_tier <> f.p""")
    issues.append(Issue("DQ-07", "accounts.plan_tier disagrees with the account's first subscription plan", "Medium", "accounts", mism, n_acc,
        f"{mism} accounts have an account-level plan different from their earliest subscription's plan.",
        "'Plan' means different things depending on the table; plan-vs-churn results use the account-level field and can differ from subscription-level plan results.",
        "Revenue by plan uses subscriptions.plan_tier (billing plan). Account-level churn segmentation uses accounts.plan_tier (labelled 'account plan as recorded').",
        ["account_churn_rate_flag", "mrr"], ["accounts", "subscriptions"]))

    # --- 6. support data ------------------------------------------------------------------------------------
    before_signup = _one(con, "SELECT count(*) FROM raw_support_tickets t JOIN raw_accounts a USING(account_id) WHERE CAST(t.submitted_at AS DATE) < a.signup_date")
    issues.append(Issue("DQ-08", "Support tickets dated before the account signed up", "Medium", "support_tickets", before_signup, n_tix,
        f"{before_signup} tickets predate the account signup date.", "Ticket timing relative to tenure is unreliable; lifetime aggregates only.",
        "Tickets used as per-account lifetime aggregates; flagged in support_tickets.dq_before_account_signup.", ["support_metrics"], ["support_tickets"]))
    resp_gt = _one(con, "SELECT count(*) FROM raw_support_tickets WHERE first_response_time_minutes/60.0 > resolution_time_hours")
    issues.append(Issue("DQ-09", "First response slower than total resolution time", "Low", "support_tickets", resp_gt, n_tix,
        f"{resp_gt} tickets have first response later than resolution.", "Small distortion of response-time averages.",
        "Kept and flagged (dq_first_response_after_resolution).", ["support_metrics"], ["support_tickets"]))
    csat_null = _one(con, "SELECT count(*) FROM raw_support_tickets WHERE satisfaction_score IS NULL")
    csat_min = _one(con, "SELECT min(satisfaction_score) FROM raw_support_tickets")
    issues.append(Issue("DQ-10", "CSAT missing for many tickets and never below 3", "Medium", "support_tickets", csat_null, n_tix,
        f"{csat_null} tickets have no CSAT; the minimum observed score is {csat_min:g} on a presumed 1-5 scale.",
        "CSAT is nonresponse-biased and unable to record dissatisfaction; poor evidence about satisfaction.",
        "CSAT averaged over responses only, with response count shown; treated as weak evidence.", ["support_metrics"], ["support_tickets"]))

    # --- 7. churn events -----------------------------------------------------------------------------------
    fb_null = _one(con, "SELECT count(*) FROM raw_churn_events WHERE feedback_text IS NULL")
    elig, match = con.execute("""SELECT count(*), coalesce(sum(CASE WHEN (reason_code='pricing' AND feedback_text='too expensive') OR (reason_code='features' AND feedback_text='missing features')
        OR (reason_code='competitor' AND feedback_text='switched to competitor') THEN 1 ELSE 0 END),0)
        FROM raw_churn_events WHERE feedback_text IS NOT NULL AND reason_code IN ('pricing','features','competitor')""").fetchone()
    issues.append(Issue("DQ-11", "Churn reason_code and free-text feedback are inconsistent", "Medium", "churn_events", elig - match, n_evt,
        f"Among events whose reason has an obvious text equivalent, only {match} of {elig} ({100 * match / max(elig, 1):.0f}%) agree; {fb_null} events have no feedback text.",
        "Churn reasons are unreliable as a causal explanation; the two reason fields disagree about as often as chance.",
        "Reported reasons use reason_code only, labelled 'recorded reason', never as proof of cause.", ["churn_reasons"], ["churn_events"]))
    dup_evt = _one(con, "SELECT count(*) FROM (SELECT account_id, churn_date FROM raw_churn_events GROUP BY 1,2 HAVING count(*) > 1)")
    issues.append(Issue("DQ-12", "Duplicate churn events for the same account and date", "Low", "churn_events", dup_evt, n_evt,
        f"{dup_evt} account/date pair(s) appear more than once.", "Negligible inflation of event counts.",
        "Later duplicates flagged (dq_duplicate_account_date) and excluded from event-based metrics; rows retained.", ["churn_reasons", "cohort_retention"], ["churn_events"]))
    react_bad = _one(con, """SELECT count(*) FROM raw_churn_events e WHERE e.is_reactivation AND NOT EXISTS
        (SELECT 1 FROM raw_churn_events p WHERE p.account_id = e.account_id AND p.churn_date < e.churn_date)""")
    if react_bad:
        issues.append(Issue("DQ-13", "is_reactivation set without any earlier churn event", "Low", "churn_events", react_bad, n_evt,
            f"{react_bad} events are flagged reactivation but have no earlier event for the account.", "Reactivation analysis unreliable.",
            "Flag documented; reactivation not used in headline metrics.", ["churn_reasons"], ["churn_events"]))
    else:
        passed.append({"check": "reactivation events always have an earlier churn event", "result": "passed"})

    # --- 8. trial flags --------------------------------------------------------------------------------------
    trial_paid = _one(con, """SELECT count(*) FROM raw_accounts a WHERE a.is_trial AND EXISTS (SELECT 1 FROM raw_subscriptions s
        WHERE s.account_id=a.account_id AND s.end_date IS NULL AND NOT s.is_trial)""")
    n_trial = _one(con, "SELECT count(*) FROM raw_accounts WHERE is_trial")
    issues.append(Issue("DQ-14", "Trial accounts that also hold open paid subscriptions", "Medium", "accounts", trial_paid, n_trial,
        f"{trial_paid} of {n_trial} accounts flagged is_trial have open paid subscriptions.", "Trial status at account level is not trustworthy.",
        "Trial-based metrics use subscriptions.is_trial; accounts.is_trial is descriptive only. Trial conversion is not reported.", ["active_paying_accounts"], ["accounts", "subscriptions"]))
    zero_dur = _one(con, "SELECT count(*) FROM raw_subscriptions WHERE end_date = start_date")
    issues.append(Issue("DQ-15", "Zero-duration subscriptions (start = end)", "Low", "subscriptions", zero_dur, n_sub,
        f"{zero_dur} subscriptions start and end on the same day.", "Never active at a month end; negligible effect.",
        "Kept and flagged (dq_zero_duration).", ["subscription_churn_rate"], ["subscriptions"]))

    # --- 9. temporal concentration ---------------------------------------------------------------------------
    last_q_ends, tot_ends = con.execute("""SELECT sum(CASE WHEN end_date >= date '2024-10-01' THEN 1 ELSE 0 END), count(*) FROM raw_subscriptions WHERE end_date IS NOT NULL""").fetchone()
    issues.append(Issue("DQ-16", "Churn activity concentrated in the final quarter", "Medium", "subscriptions/churn_events", int(last_q_ends or 0), int(tot_ends or 0),
        f"{int(last_q_ends or 0)} of {tot_ends} subscription end dates fall in Q4 2024.",
        "Time trends in churn are dominated by one quarter; cohort comparisons are confounded by calendar time. May be a generation artifact.",
        "Surfaced as an 'unusual change' in the copilot; no claim that churn is structurally rising.", ["subscription_churn_rate", "gross_mrr_churn_rate", "cohort_retention"], ["subscriptions", "churn_events"]))

    # --- 10. pricing structure / gaps --------------------------------------------------------------------------
    issues.append(Issue("DQ-17", "ARR is always 12 x MRR; annual billing has the same price as monthly", "Medium", "subscriptions", n_sub, n_sub,
        "arr_amount is derived from mrr_amount for every row; annual and monthly billing carry identical per-seat prices.",
        "ARR here is annualised MRR (a run-rate), not contracted annual value or cash collected.",
        "ARR labelled 'annualised run-rate = 12 x MRR'.", ["arr"], ["subscriptions"]))
    issues.append(Issue("DQ-18", "No payment, invoice or refund-to-invoice data", "High", "dataset", 0, 0,
        "There are no invoices, payments or collections; only a refund amount on churn events.",
        "Realized (cash) revenue, failed payments, collections and true LTV cannot be computed. Subscription amounts are bookings-like.",
        "Revenue metrics are labelled 'recorded subscription value'; cash revenue and LTV marked unavailable.", ["mrr", "arr", "cash_revenue", "customer_lifetime_value"], ["subscriptions"]))
    issues.append(Issue("DQ-19", "No marketing spend or user-level data", "Medium", "dataset", 0, 0,
        "There is no acquisition cost, no individual users, sessions or onboarding events.",
        "CAC, channel ROI, activation and user-level engagement are not computable.", "Marked unavailable in the metric registry.",
        ["cac", "feature_engagement"], []))
    issues.append(Issue("DQ-20", "Dataset ZIP carries no README or license", "Info", "dataset", 0, 0,
        "The supplied ZIP contains only five CSVs. Synthetic origin and licence terms were NOT confirmed from the package.",
        "Cannot verify provenance from the data files themselves (company names are placeholders such as 'Company_0', which is consistent with synthetic data).",
        "UI states the data is 'synthetic per the dataset's public listing (not confirmed by the supplied files)'. Verify the licence on the Kaggle page before redistribution.", [], []))
    # outliers (informational)
    mx = con.execute("SELECT max(seats), max(mrr_amount) FROM raw_subscriptions").fetchone()
    issues.append(Issue("DQ-21", "Large seat counts and MRR values (outliers)", "Info", "subscriptions", 0, n_sub,
        f"Max seats {mx[0]}, max single-row MRR ${mx[1]:,.0f}.", "Explained by seat-based pricing (mrr = seats x per-seat price: Basic 19, Pro 49, Enterprise 199); not errors.",
        "Kept.", ["mrr"], ["subscriptions"]))
    return issues, passed


def run_audit(raw_dir: Path) -> dict:
    con = duckdb.connect(":memory:")
    try:
        register_raw_views(con, raw_dir)
        profile = profile_tables(con)
        issues, passed = run_checks(con)
        # Join cardinality evidence (explains why naive joins double count)
        card = {
            "subscriptions_per_account_avg": _one(con, "SELECT round(avg(c),2) FROM (SELECT count(*) c FROM raw_subscriptions GROUP BY account_id)"),
            "tickets_per_account_avg": _one(con, "SELECT round(count(*)*1.0/(SELECT count(*) FROM raw_accounts),2) FROM raw_support_tickets"),
            "usage_rows_per_subscription_avg": _one(con, "SELECT round(count(*)*1.0/(SELECT count(*) FROM raw_subscriptions),2) FROM raw_feature_usage"),
            "churn_events_per_account_avg": _one(con, "SELECT round(count(*)*1.0/(SELECT count(*) FROM raw_accounts),2) FROM raw_churn_events"),
        }
    finally:
        con.close()
    order = {s: i for i, s in enumerate(SEVERITIES)}
    issues.sort(key=lambda i: (order[i.severity], -i.affected))
    return {"profile": profile, "issues": [dict(asdict(i), pct=i.pct) for i in issues], "passed_checks": passed,
            "join_cardinality": card}


def write_report(audit: dict, md_path: Path, json_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(audit, indent=2, default=str))
    L = ["# Data Quality Report", "", "_Generated by `python -m src.data.pipeline` from the raw CSVs. Raw files are never modified._", ""]
    L += ["## Dataset profile", "", "| Table | File | Rows | Columns |", "|---|---|---:|---:|"]
    L += [f"| {p['table']} | {p['file']} | {p['rows']:,} | {p['columns']} |" for p in audit["profile"]]
    L += ["", "## Join cardinality (why naive joins double count)", ""]
    L += [f"- {k.replace('_', ' ')}: **{v}**" for k, v in audit["join_cardinality"].items()]
    L += ["", "Aggregations across child tables therefore pre-aggregate to account grain (`account_features`) before joining.", "",
          "## Issues", "", "| ID | Severity | Table | Affected | Issue | Impact | Handling |", "|---|---|---|---:|---|---|---|"]
    for i in audit["issues"]:
        aff = f"{i['affected']:,} ({i['pct']}%)" if i["total"] else "n/a (dataset gap)"
        L.append(f"| {i['id']} | {i['severity']} | {i['table']} | {aff} | **{i['title']}**. {i['description']} | {i['impact']} | {i['handling']} |")
    L += ["", "## Checks that passed", ""] + [f"- {c['check']}" for c in audit["passed_checks"]]
    md_path.write_text("\n".join(L) + "\n")
