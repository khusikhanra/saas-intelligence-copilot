"""Cleaning pipeline: raw CSV -> cleaned analytical tables + marts in DuckDB.

Principles
- Raw files are never modified. Cleaning ADDS data-quality flags and derived columns; it never deletes rows.
- Rows flagged as problematic stay in the tables; metrics decide explicitly whether to exclude them
  (see src/metrics/definitions.py) and the choice is documented.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

from .loader import raw_manifest, register_raw_views, write_manifest

# Price points implied by the data (verified in the audit: mrr_amount == seats * price for every paid row).
PLAN_PRICE_PER_SEAT = {"Basic": 19, "Pro": 49, "Enterprise": 199}

CLEAN_SQL = [
    # --- churn events: flag exact duplicates (same account + same day); keep the rows -------------
    """CREATE TABLE churn_events AS
       SELECT e.*,
              (row_number() OVER (PARTITION BY account_id, churn_date ORDER BY churn_event_id) > 1) AS dq_duplicate_account_date,
              date_trunc('month', churn_date)::DATE AS churn_month
       FROM raw_churn_events e""",
    # --- accounts: add event-derived fields and the churn-indicator conflict flag -----------------
    """CREATE TABLE accounts AS
       SELECT a.*,
              coalesce(e.n_events, 0) AS n_churn_events,
              e.first_churn_date, e.last_churn_date,
              coalesce(e.n_events, 0) > 0 AS has_churn_event,
              (a.churn_flag <> (coalesce(e.n_events, 0) > 0)) AS dq_churn_flag_conflict,
              date_trunc('month', a.signup_date)::DATE AS signup_month,
              date_trunc('quarter', a.signup_date)::DATE AS signup_quarter,
              CASE WHEN a.seats <= 5 THEN '1-5' WHEN a.seats <= 20 THEN '6-20'
                   WHEN a.seats <= 50 THEN '21-50' ELSE '51+' END AS seats_band
       FROM raw_accounts a
       LEFT JOIN (SELECT account_id, count(*) AS n_events, min(churn_date) AS first_churn_date,
                         max(churn_date) AS last_churn_date
                  FROM churn_events WHERE NOT dq_duplicate_account_date GROUP BY 1) e USING (account_id)""",
    # --- subscriptions -----------------------------------------------------------------------------
    """CREATE TABLE subscriptions AS
       SELECT s.*,
              NOT s.is_trial AS is_paid,
              (s.end_date IS NULL) AS is_open,
              date_trunc('month', s.start_date)::DATE AS start_month,
              date_diff('day', s.start_date, coalesce(s.end_date, (SELECT max_date FROM _snapshot))) AS duration_days,
              (s.end_date IS NOT NULL AND s.end_date = s.start_date) AS dq_zero_duration,
              (s.end_date IS NOT NULL AND s.end_date < s.start_date) AS dq_end_before_start
       FROM raw_subscriptions s""",
    # --- support tickets ---------------------------------------------------------------------------
    """CREATE TABLE support_tickets AS
       SELECT t.*,
              date_trunc('month', t.submitted_at)::DATE AS submitted_month,
              t.satisfaction_score IS NOT NULL AS has_csat,
              (t.first_response_time_minutes / 60.0 > t.resolution_time_hours) AS dq_first_response_after_resolution,
              (CAST(t.submitted_at AS DATE) < a.signup_date) AS dq_before_account_signup
       FROM raw_support_tickets t JOIN raw_accounts a USING (account_id)""",
    # --- feature usage (account_id is denormalised 1:1 from the owning subscription) -----------------
    """CREATE TABLE feature_usage AS
       SELECT row_number() OVER () AS usage_row_id,
              u.*, s.account_id,
              date_trunc('month', u.usage_date)::DATE AS usage_month,
              (count(*) OVER (PARTITION BY u.usage_id) > 1) AS dq_duplicate_usage_id,
              (u.usage_date < s.start_date) AS dq_before_subscription_start,
              (s.end_date IS NOT NULL AND u.usage_date > s.end_date) AS dq_after_subscription_end,
              (u.usage_count = 0) AS dq_zero_usage
       FROM raw_feature_usage u JOIN raw_subscriptions s USING (subscription_id)""",
]

MART_SQL = [
    # --- one row per account; every aggregate subquery is 1 row per account => no fan-out ----------
    """CREATE TABLE account_features AS
       SELECT a.account_id, a.account_name, a.industry, a.country, a.referral_source, a.plan_tier, a.seats,
              a.seats_band, a.is_trial, a.churn_flag, a.signup_date, a.signup_month, a.signup_quarter,
              a.n_churn_events, a.has_churn_event, a.first_churn_date, a.dq_churn_flag_conflict,
              s.n_subscriptions, s.n_paid_subscriptions, coalesce(s.n_open_paid_subscriptions, 0) AS n_open_paid_subscriptions,
              coalesce(s.mrr_open_paid, 0) AS mrr_at_snapshot, s.booked_value_to_date,
              u.usage_events, u.total_usage_count, u.distinct_features, u.total_duration_hours, u.error_count,
              t.n_tickets, t.avg_first_response_min, t.avg_resolution_hours, t.avg_csat, t.n_escalations
       FROM accounts a
       LEFT JOIN (SELECT account_id, count(*) AS n_subscriptions, sum(is_paid::INT) AS n_paid_subscriptions,
                         sum((is_paid AND is_open)::INT) AS n_open_paid_subscriptions,
                         sum(CASE WHEN is_paid AND is_open THEN mrr_amount END) AS mrr_open_paid,
                         sum(CASE WHEN is_paid THEN mrr_amount * duration_days / 30.4375 END) AS booked_value_to_date
                  FROM subscriptions GROUP BY 1) s USING (account_id)
       LEFT JOIN (SELECT account_id, count(*) AS usage_events, sum(usage_count) AS total_usage_count,
                         count(DISTINCT feature_name) AS distinct_features,
                         sum(usage_duration_secs) / 3600.0 AS total_duration_hours, sum(error_count) AS error_count
                  FROM feature_usage GROUP BY 1) u USING (account_id)
       LEFT JOIN (SELECT account_id, count(*) AS n_tickets, avg(first_response_time_minutes) AS avg_first_response_min,
                         avg(resolution_time_hours) AS avg_resolution_hours, avg(satisfaction_score) AS avg_csat,
                         sum(escalation_flag::INT) AS n_escalations
                  FROM support_tickets GROUP BY 1) t USING (account_id)""",
    # --- subscription grain enriched with ACCOUNT attributes (many subs -> one account: no fan-out) ---
    """CREATE VIEW sub_enriched AS
       SELECT s.*, a.industry, a.country, a.referral_source, a.plan_tier AS account_plan_tier,
              a.churn_flag AS account_churn_flag, a.signup_date
       FROM subscriptions s JOIN accounts a USING (account_id)""",
]

ALLOWED_OBJECTS = ["accounts", "subscriptions", "churn_events", "support_tickets", "feature_usage",
                   "account_features", "sub_enriched"]


def build_database(raw_dir: Path, db_path: Path, manifest_path: Path | None = None) -> dict:
    """Build the analytical database atomically (temp file -> rename). Returns summary stats."""
    raw_dir, db_path = Path(raw_dir), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_suffix(".building")
    for p in (tmp, Path(str(tmp) + ".wal")):
        if p.exists():
            p.unlink()
    manifest = raw_manifest(raw_dir)  # also validates files/headers
    con = duckdb.connect(str(tmp))
    try:
        register_raw_views(con, raw_dir)
        con.execute("""CREATE TABLE _snapshot AS SELECT greatest(
              (SELECT max(start_date) FROM raw_subscriptions), (SELECT max(end_date) FROM raw_subscriptions),
              (SELECT max(churn_date) FROM raw_churn_events), (SELECT max(usage_date) FROM raw_feature_usage),
              (SELECT max(CAST(submitted_at AS DATE)) FROM raw_support_tickets),
              (SELECT max(signup_date) FROM raw_accounts)) AS max_date""")
        for sql in CLEAN_SQL + MART_SQL:
            con.execute(sql)
        con.execute("CREATE TABLE _meta AS SELECT 'snapshot_date' AS key, CAST(max_date AS VARCHAR) AS value FROM _snapshot")
        # raw views reference the CSV path; drop them so the DB file is self-contained
        for t in ("accounts", "subscriptions", "churn_events", "support_tickets", "feature_usage"):
            con.execute(f"DROP VIEW IF EXISTS raw_{t}")
        stats = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ALLOWED_OBJECTS}
        stats["snapshot_date"] = str(con.execute("SELECT max_date FROM _snapshot").fetchone()[0])
        con.close()
    except Exception:
        con.close()
        if tmp.exists():
            tmp.unlink()
        raise
    os.replace(tmp, db_path)
    if manifest_path:
        write_manifest(manifest, Path(manifest_path))
    return stats
