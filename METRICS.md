# Metric definitions

_Generated from `src/metrics/definitions.py` by `python scripts/gen_docs.py`. Do not edit by hand._

Status key: **available** direct; **caveated** computed but a known data issue changes interpretation; **approximate** proxy; **unavailable** not computed.

| Metric | Status |
|---|---|
| Monthly recurring revenue (MRR, as recorded) | caveated |
| Annualised run-rate (ARR = 12 x MRR) | caveated |
| Active paying accounts | caveated |
| Active paid subscriptions | caveated |
| New paid subscriptions / new MRR | caveated |
| Account churn rate (flag-based) - PRIMARY | caveated |
| Account churn rate (event-based) - ALTERNATIVE | caveated |
| Monthly subscription (logo) churn rate | caveated |
| MRR / accounts by plan, industry, country, referral source | caveated |
| Recorded churn reasons | caveated |
| Support response/resolution time, CSAT, escalations | caveated |
| Recorded upgrade / downgrade flags | approximate |
| Gross MRR churn rate (revenue churn) | approximate |
| Cohort retention (event-based) | approximate |
| Feature engagement and association with churn | approximate |
| Recorded subscription value to date (bookings-based) | approximate |
| Realized / cash revenue | unavailable |
| Customer lifetime value (CLV/LTV) | unavailable |
| Net revenue retention / expansion MRR | unavailable |
| Customer acquisition cost / channel ROI | unavailable |
| Trial-to-paid conversion | unavailable |

## Monthly recurring revenue (MRR, as recorded)
`mrr` - status: **caveated**

- **Formula:** SUM(mrr_amount) over PAID subscription rows (is_trial = false) that are active on the date: start_date <= D AND (end_date IS NULL OR end_date > D).
- **Date basis:** As-of date D (month end for trends; snapshot date = latest date present in any table (2024-12-31 for the supplied data)).
- **Grain:** Subscription row -> summed to month/segment
- **Filters:** Paid subscriptions only. Industry/country/referral filters come from the owning account; plan filter uses subscriptions.plan_tier.
- **Assumptions:** Each subscription row is treated as a concurrent recurring line item (the data holds ~10 rows per account, ~8 open at once). A subscription ending on D is NOT active on D.
- **Limitations:** Recorded subscription amounts, not cash collected. If rows are really successive billing periods, MRR is overstated; alternative definitions are shown on the Methodology page. Includes MRR of accounts flagged churned.
- **Related data-quality issues:** DQ-02, DQ-03, DQ-18

## Annualised run-rate (ARR = 12 x MRR)
`arr` - status: **caveated**

- **Formula:** 12 x MRR (identical to SUM(arr_amount) over the same rows).
- **Date basis:** Same as MRR.
- **Grain:** Same as MRR
- **Filters:** Same as MRR
- **Assumptions:** ARR is derived (arr_amount is always exactly 12 x mrr_amount).
- **Limitations:** Run-rate, not contracted annual value; annual billing carries no different price. Not cash or bookings.
- **Related data-quality issues:** DQ-17, DQ-18

## Active paying accounts
`active_paying_accounts` - status: **caveated**

- **Formula:** COUNT(DISTINCT account_id) with at least one active paid subscription on date D.
- **Date basis:** As-of date D.
- **Grain:** Account
- **Filters:** As MRR
- **Assumptions:** Account is paying if any paid subscription row is active.
- **Limitations:** Accounts flagged churned still hold open paid subscriptions, so this count does not drop when churn_flag is set.
- **Related data-quality issues:** DQ-01, DQ-02, DQ-14

## Active paid subscriptions
`active_paid_subscriptions` - status: **caveated**

- **Formula:** COUNT(*) of paid subscription rows active on date D.
- **Date basis:** As-of date D.
- **Grain:** Subscription
- **Filters:** As MRR
- **Assumptions:** Rows are concurrent subscriptions.
- **Limitations:** Overlapping periods per account (DQ-03).
- **Related data-quality issues:** DQ-03

## New paid subscriptions / new MRR
`new_subscriptions` - status: **caveated**

- **Formula:** COUNT(*) and SUM(mrr_amount) of paid subscription rows with start_date inside the month.
- **Date basis:** start_date month.
- **Grain:** Subscription
- **Filters:** As MRR
- **Assumptions:** Every started row counts as new; no distinction between new logo, expansion or renewal rows.
- **Limitations:** Cannot separate new customers from additional subscriptions of existing customers.
- **Related data-quality issues:** DQ-03

## Account churn rate (flag-based) - PRIMARY
`account_churn_rate_flag` - status: **caveated**

- **Formula:** SUM(accounts.churn_flag) / COUNT(accounts).
- **Date basis:** Snapshot (no time dimension).
- **Grain:** Account
- **Filters:** Industry, country, referral source, account plan (as recorded), seat band; signup-date range.
- **Assumptions:** churn_flag means 'account churned at some point'.
- **Limitations:** Conflicts with churn_events for 62% of accounts and with open paid subscriptions (DQ-01/02); no time dimension, so rates reflect cumulative exposure (older accounts had longer to churn).
- **Related data-quality issues:** DQ-01, DQ-02, DQ-07

## Account churn rate (event-based) - ALTERNATIVE
`account_churn_rate_event` - status: **caveated**

- **Formula:** COUNT(accounts with >= 1 non-duplicate churn_event) / COUNT(accounts).
- **Date basis:** Event churn_date.
- **Grain:** Account
- **Filters:** As the flag-based metric
- **Assumptions:** An account with any churn event is counted as having churned.
- **Limitations:** Includes events for accounts not flagged churned, probably reactivation cycles; far higher than the flag-based rate.
- **Related data-quality issues:** DQ-01, DQ-12

## Monthly subscription (logo) churn rate
`subscription_churn_rate` - status: **caveated**

- **Formula:** Subscriptions active at the previous month end whose end_date falls in the month / subscriptions active at the previous month end.
- **Date basis:** end_date month.
- **Grain:** Subscription
- **Filters:** As MRR
- **Assumptions:** Opening-cohort definition: subscriptions started and ended inside the same month are excluded.
- **Limitations:** Very small opening base in early months produces unstable rates; Q4-2024 concentration (DQ-16).
- **Related data-quality issues:** DQ-03, DQ-16

## MRR / accounts by plan, industry, country, referral source
`mrr_breakdown` - status: **caveated**

- **Formula:** MRR definition above, grouped by the chosen dimension (plan = subscription plan_tier).
- **Date basis:** As-of date D.
- **Grain:** Subscription -> segment
- **Filters:** As MRR
- **Assumptions:** Account attributes joined many-subscriptions-to-one-account (no fan-out).
- **Limitations:** Account-level plan field disagrees with subscription plan for 66% of accounts (DQ-07).
- **Related data-quality issues:** DQ-03, DQ-07

## Recorded churn reasons
`churn_reasons` - status: **caveated**

- **Formula:** COUNT(*) of non-duplicate churn events by reason_code.
- **Date basis:** churn_date.
- **Grain:** Churn event
- **Filters:** Date range
- **Assumptions:** reason_code is the recorded reason.
- **Limitations:** reason_code disagrees with free-text feedback about as often as chance (DQ-11); treat as a label, not a cause.
- **Related data-quality issues:** DQ-11, DQ-12

## Support response/resolution time, CSAT, escalations
`support_metrics` - status: **caveated**

- **Formula:** AVG(first_response_time_minutes), AVG(resolution_time_hours), AVG(satisfaction_score) over responded tickets, escalation rate.
- **Date basis:** submitted_at month.
- **Grain:** Ticket (account-level aggregates for churn analysis)
- **Filters:** Account attributes
- **Assumptions:** Averages are unweighted ticket means.
- **Limitations:** CSAT is 59% response rate and never below 3 (DQ-10); 54% of tickets predate account signup (DQ-08).
- **Related data-quality issues:** DQ-08, DQ-09, DQ-10

## Recorded upgrade / downgrade flags
`upgrades_downgrades_recorded` - status: **approximate**

- **Formula:** COUNT(*) of subscription rows with upgrade_flag / downgrade_flag = true.
- **Date basis:** start_date month.
- **Grain:** Subscription
- **Filters:** Paid + trial
- **Assumptions:** Flags are taken at face value.
- **Limitations:** Flags are not tied to a plan or MRR change between rows; do not infer expansion/contraction revenue.
- **Related data-quality issues:** DQ-03

## Gross MRR churn rate (revenue churn)
`gross_mrr_churn_rate` - status: **approximate**

- **Formula:** MRR of opening subscriptions that ended in the month / opening MRR.
- **Date basis:** end_date month.
- **Grain:** Subscription
- **Filters:** As MRR
- **Assumptions:** Same opening-cohort definition; no expansion offset.
- **Limitations:** Contraction (downgrades) and expansion cannot be measured reliably so NRR is unavailable; this is gross churn only.
- **Related data-quality issues:** DQ-03, DQ-16

## Cohort retention (event-based)
`cohort_retention` - status: **approximate**

- **Formula:** Share of a signup-quarter cohort with NO churn event within k months of its own signup date. A cohort is shown at month k only if every member is observable for k months before the snapshot.
- **Date basis:** Account signup_date + churn_date.
- **Grain:** Account (cohort = signup quarter)
- **Filters:** Signup range, segments
- **Assumptions:** First non-duplicate churn event is the churn time; reactivation ignored.
- **Limitations:** Event-based and conflicts with churn_flag; churn events cluster in late 2024 for every cohort so cohort comparisons are confounded by calendar time (DQ-16).
- **Related data-quality issues:** DQ-01, DQ-16

## Feature engagement and association with churn
`feature_engagement` - status: **approximate**

- **Formula:** Per account: total usage_count, distinct features, events, errors over ALL usage rows of the account's subscriptions (lifetime aggregate); compared across churn status.
- **Date basis:** Lifetime (no time alignment).
- **Grain:** Account
- **Filters:** Account attributes
- **Assumptions:** Lifetime aggregates only.
- **Limitations:** 78% of usage rows fall outside their subscription window (DQ-04); no user-level data; association is not causation.
- **Related data-quality issues:** DQ-04, DQ-05

## Recorded subscription value to date (bookings-based)
`booked_value_to_date` - status: **approximate**

- **Formula:** SUM(mrr_amount x days active / 30.4375) over paid subscription rows through min(end_date, snapshot).
- **Date basis:** Subscription lifetime.
- **Grain:** Subscription -> account
- **Filters:** As MRR
- **Assumptions:** Days between start and end/snapshot at the recorded MRR.
- **Limitations:** NOT cash revenue and NOT customer lifetime value: no payments, no discounting, no expected future life.
- **Related data-quality issues:** DQ-03, DQ-18

## Realized / cash revenue
`cash_revenue` - status: **unavailable**

- **Formula:** n/a - no payments, invoices or collections in the data.
- **Date basis:** n/a
- **Grain:** n/a
- **Filters:** n/a
- **Assumptions:** n/a
- **Limitations:** Subscription amounts must not be presented as cash revenue.
- **Related data-quality issues:** DQ-18

## Customer lifetime value (CLV/LTV)
`customer_lifetime_value` - status: **unavailable**

- **Formula:** n/a - needs realized revenue, margin and a reliable retention curve.
- **Date basis:** n/a
- **Grain:** n/a
- **Filters:** n/a
- **Assumptions:** n/a
- **Limitations:** Only the bookings-based 'recorded value to date' proxy is offered; it is not CLV.
- **Related data-quality issues:** DQ-18, DQ-01

## Net revenue retention / expansion MRR
`net_revenue_retention` - status: **unavailable**

- **Formula:** n/a - plan/seat changes are not linked between subscription rows.
- **Date basis:** n/a
- **Grain:** n/a
- **Filters:** n/a
- **Assumptions:** n/a
- **Limitations:** upgrade/downgrade flags are not tied to MRR changes (DQ-03).
- **Related data-quality issues:** DQ-03

## Customer acquisition cost / channel ROI
`cac` - status: **unavailable**

- **Formula:** n/a - no marketing or sales spend.
- **Date basis:** n/a
- **Grain:** n/a
- **Filters:** n/a
- **Assumptions:** n/a
- **Limitations:** Channels can only be compared on volume, revenue and churn, not efficiency.
- **Related data-quality issues:** DQ-19

## Trial-to-paid conversion
`trial_conversion` - status: **unavailable**

- **Formula:** n/a - trial flags are inconsistent across tables.
- **Date basis:** n/a
- **Grain:** n/a
- **Filters:** n/a
- **Assumptions:** n/a
- **Limitations:** Every flagged-trial account also holds open paid subscriptions (DQ-14).
- **Related data-quality issues:** DQ-14
