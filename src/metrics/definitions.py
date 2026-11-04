"""Central metric registry (the semantic layer's documentation + status).

Statuses
  available   computed directly from fields that mean what the metric needs
  caveated    computed, but a known data-quality issue changes how it must be read (see related_dq)
  approximate computed with a proxy / simplification; do not read as precise
  unavailable the dataset does not contain the fields needed; NOT computed
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    status: str
    formula: str
    date_basis: str
    grain: str
    filters: str
    assumptions: str
    limitations: str
    related_dq: tuple = field(default_factory=tuple)


SNAP = "snapshot date = latest date present in any table (2024-12-31 for the supplied data)"

METRICS: dict[str, Metric] = {m.id: m for m in [
    Metric("mrr", "Monthly recurring revenue (MRR, as recorded)", "caveated",
           "SUM(mrr_amount) over PAID subscription rows (is_trial = false) that are active on the date: start_date <= D AND (end_date IS NULL OR end_date > D).",
           "As-of date D (month end for trends; " + SNAP + ").", "Subscription row -> summed to month/segment",
           "Paid subscriptions only. Industry/country/referral filters come from the owning account; plan filter uses subscriptions.plan_tier.",
           "Each subscription row is treated as a concurrent recurring line item (the data holds ~10 rows per account, ~8 open at once). A subscription ending on D is NOT active on D.",
           "Recorded subscription amounts, not cash collected. If rows are really successive billing periods, MRR is overstated; alternative definitions are shown on the Methodology page. Includes MRR of accounts flagged churned.",
           ("DQ-02", "DQ-03", "DQ-18")),
    Metric("arr", "Annualised run-rate (ARR = 12 x MRR)", "caveated",
           "12 x MRR (identical to SUM(arr_amount) over the same rows).", "Same as MRR.", "Same as MRR", "Same as MRR",
           "ARR is derived (arr_amount is always exactly 12 x mrr_amount).",
           "Run-rate, not contracted annual value; annual billing carries no different price. Not cash or bookings.", ("DQ-17", "DQ-18")),
    Metric("active_paying_accounts", "Active paying accounts", "caveated",
           "COUNT(DISTINCT account_id) with at least one active paid subscription on date D.", "As-of date D.", "Account", "As MRR",
           "Account is paying if any paid subscription row is active.", "Accounts flagged churned still hold open paid subscriptions, so this count does not drop when churn_flag is set.",
           ("DQ-01", "DQ-02", "DQ-14")),
    Metric("active_paid_subscriptions", "Active paid subscriptions", "caveated",
           "COUNT(*) of paid subscription rows active on date D.", "As-of date D.", "Subscription", "As MRR",
           "Rows are concurrent subscriptions.", "Overlapping periods per account (DQ-03).", ("DQ-03",)),
    Metric("new_subscriptions", "New paid subscriptions / new MRR", "caveated",
           "COUNT(*) and SUM(mrr_amount) of paid subscription rows with start_date inside the month.", "start_date month.", "Subscription", "As MRR",
           "Every started row counts as new; no distinction between new logo, expansion or renewal rows.",
           "Cannot separate new customers from additional subscriptions of existing customers.", ("DQ-03",)),
    Metric("upgrades_downgrades_recorded", "Recorded upgrade / downgrade flags", "approximate",
           "COUNT(*) of subscription rows with upgrade_flag / downgrade_flag = true.", "start_date month.", "Subscription", "Paid + trial",
           "Flags are taken at face value.", "Flags are not tied to a plan or MRR change between rows; do not infer expansion/contraction revenue.", ("DQ-03",)),
    Metric("account_churn_rate_flag", "Account churn rate (flag-based) - PRIMARY", "caveated",
           "SUM(accounts.churn_flag) / COUNT(accounts).", "Snapshot (no time dimension).", "Account",
           "Industry, country, referral source, account plan (as recorded), seat band; signup-date range.",
           "churn_flag means 'account churned at some point'.", "Conflicts with churn_events for 62% of accounts and with open paid subscriptions (DQ-01/02); no time dimension, so rates reflect cumulative exposure (older accounts had longer to churn).",
           ("DQ-01", "DQ-02", "DQ-07")),
    Metric("account_churn_rate_event", "Account churn rate (event-based) - ALTERNATIVE", "caveated",
           "COUNT(accounts with >= 1 non-duplicate churn_event) / COUNT(accounts).", "Event churn_date.", "Account", "As the flag-based metric",
           "An account with any churn event is counted as having churned.", "Includes events for accounts not flagged churned, probably reactivation cycles; far higher than the flag-based rate.", ("DQ-01", "DQ-12")),
    Metric("subscription_churn_rate", "Monthly subscription (logo) churn rate", "caveated",
           "Subscriptions active at the previous month end whose end_date falls in the month / subscriptions active at the previous month end.", "end_date month.", "Subscription", "As MRR",
           "Opening-cohort definition: subscriptions started and ended inside the same month are excluded.", "Very small opening base in early months produces unstable rates; Q4-2024 concentration (DQ-16).", ("DQ-03", "DQ-16")),
    Metric("gross_mrr_churn_rate", "Gross MRR churn rate (revenue churn)", "approximate",
           "MRR of opening subscriptions that ended in the month / opening MRR.", "end_date month.", "Subscription", "As MRR",
           "Same opening-cohort definition; no expansion offset.", "Contraction (downgrades) and expansion cannot be measured reliably so NRR is unavailable; this is gross churn only.", ("DQ-03", "DQ-16")),
    Metric("cohort_retention", "Cohort retention (event-based)", "approximate",
           "Share of a signup-quarter cohort with NO churn event within k months of its own signup date. A cohort is shown at month k only if every member is observable for k months before the snapshot.",
           "Account signup_date + churn_date.", "Account (cohort = signup quarter)", "Signup range, segments",
           "First non-duplicate churn event is the churn time; reactivation ignored.", "Event-based and conflicts with churn_flag; churn events cluster in late 2024 for every cohort so cohort comparisons are confounded by calendar time (DQ-16).",
           ("DQ-01", "DQ-16")),
    Metric("mrr_breakdown", "MRR / accounts by plan, industry, country, referral source", "caveated",
           "MRR definition above, grouped by the chosen dimension (plan = subscription plan_tier).", "As-of date D.", "Subscription -> segment", "As MRR",
           "Account attributes joined many-subscriptions-to-one-account (no fan-out).", "Account-level plan field disagrees with subscription plan for 66% of accounts (DQ-07).", ("DQ-03", "DQ-07")),
    Metric("churn_reasons", "Recorded churn reasons", "caveated",
           "COUNT(*) of non-duplicate churn events by reason_code.", "churn_date.", "Churn event", "Date range",
           "reason_code is the recorded reason.", "reason_code disagrees with free-text feedback about as often as chance (DQ-11); treat as a label, not a cause.", ("DQ-11", "DQ-12")),
    Metric("support_metrics", "Support response/resolution time, CSAT, escalations", "caveated",
           "AVG(first_response_time_minutes), AVG(resolution_time_hours), AVG(satisfaction_score) over responded tickets, escalation rate.", "submitted_at month.", "Ticket (account-level aggregates for churn analysis)", "Account attributes",
           "Averages are unweighted ticket means.", "CSAT is 59% response rate and never below 3 (DQ-10); 54% of tickets predate account signup (DQ-08).", ("DQ-08", "DQ-09", "DQ-10")),
    Metric("feature_engagement", "Feature engagement and association with churn", "approximate",
           "Per account: total usage_count, distinct features, events, errors over ALL usage rows of the account's subscriptions (lifetime aggregate); compared across churn status.", "Lifetime (no time alignment).", "Account", "Account attributes",
           "Lifetime aggregates only.", "78% of usage rows fall outside their subscription window (DQ-04); no user-level data; association is not causation.", ("DQ-04", "DQ-05")),
    Metric("booked_value_to_date", "Recorded subscription value to date (bookings-based)", "approximate",
           "SUM(mrr_amount x days active / 30.4375) over paid subscription rows through min(end_date, snapshot).", "Subscription lifetime.", "Subscription -> account", "As MRR",
           "Days between start and end/snapshot at the recorded MRR.", "NOT cash revenue and NOT customer lifetime value: no payments, no discounting, no expected future life.", ("DQ-03", "DQ-18")),
    Metric("cash_revenue", "Realized / cash revenue", "unavailable", "n/a - no payments, invoices or collections in the data.", "n/a", "n/a", "n/a", "n/a",
           "Subscription amounts must not be presented as cash revenue.", ("DQ-18",)),
    Metric("customer_lifetime_value", "Customer lifetime value (CLV/LTV)", "unavailable", "n/a - needs realized revenue, margin and a reliable retention curve.", "n/a", "n/a", "n/a", "n/a",
           "Only the bookings-based 'recorded value to date' proxy is offered; it is not CLV.", ("DQ-18", "DQ-01")),
    Metric("net_revenue_retention", "Net revenue retention / expansion MRR", "unavailable", "n/a - plan/seat changes are not linked between subscription rows.", "n/a", "n/a", "n/a", "n/a",
           "upgrade/downgrade flags are not tied to MRR changes (DQ-03).", ("DQ-03",)),
    Metric("cac", "Customer acquisition cost / channel ROI", "unavailable", "n/a - no marketing or sales spend.", "n/a", "n/a", "n/a", "n/a",
           "Channels can only be compared on volume, revenue and churn, not efficiency.", ("DQ-19",)),
    Metric("trial_conversion", "Trial-to-paid conversion", "unavailable", "n/a - trial flags are inconsistent across tables.", "n/a", "n/a", "n/a", "n/a",
           "Every flagged-trial account also holds open paid subscriptions (DQ-14).", ("DQ-14",)),
]}

STATUS_ORDER = ["available", "caveated", "approximate", "unavailable"]


def registry_rows() -> list[dict]:
    return [asdict(m) for m in METRICS.values()]


def unavailable_metrics() -> list[Metric]:
    return [m for m in METRICS.values() if m.status in ("unavailable", "approximate")]
