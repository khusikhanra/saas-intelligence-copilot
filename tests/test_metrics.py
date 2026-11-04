"""Metric tests against hand-derived values from the tiny dataset (see conftest comments).

Tiny-dataset truths (snapshot 2024-04-30):
  MRR month-end: Jan 136, Feb 403, Mar 305, Apr 286   (trial S6 excluded; S3 ended 03-15; S5 ended 04-10)
  New subs: Jan 2 (136), Feb 3 (267).  Churned (opening cohort): Mar 1 sub / 98 MRR of 5 / 403; Apr 1 / 19 of 4 / 305.
  Account churn_flag: 2 of 4 = 50%.  Non-duplicate churn events: 3 (A2, A3, A4).
"""
from datetime import date

import math
import pandas as pd
import pytest

from src.metrics import calculations as C
from src.metrics.calculations import Filters
from src.metrics.stats import chi_square_churn, describe_p, mann_whitney, wilson_interval


def test_snapshot_date(tiny_con):
    assert C.snapshot_date(tiny_con) == date(2024, 4, 30)


def test_mrr_at_dates(tiny_con):
    assert C.mrr_at(tiny_con, date(2024, 4, 30))["mrr"] == 286
    assert C.mrr_at(tiny_con, date(2024, 3, 31))["mrr"] == 305
    assert C.mrr_at(tiny_con, date(2024, 2, 29))["mrr"] == 403
    assert C.mrr_at(tiny_con, date(2024, 1, 31))["mrr"] == 136
    assert C.mrr_at(tiny_con, date(2023, 12, 31))["mrr"] == 0


def test_subscription_ending_on_date_is_not_active_that_day(tiny_con):
    assert C.mrr_at(tiny_con, date(2024, 3, 14))["mrr"] == 403
    assert C.mrr_at(tiny_con, date(2024, 3, 15))["mrr"] == 305   # S3 ends 03-15


def test_trials_excluded_from_mrr_and_counts(tiny_con):
    r = C.mrr_at(tiny_con, date(2024, 4, 30))
    assert r["active_paid_subs"] == 3 and r["active_paying_accounts"] == 2  # S1,S2 (A1) + S4 (A3); S6 trial ignored
    assert r["arr"] == 286 * 12


def test_monthly_series_values_and_mom(tiny_con):
    s = C.monthly_series(tiny_con)
    assert list(s["mrr"]) == [136, 403, 305, 286]
    assert list(s["new_subs"]) == [2, 3, 0, 0] and list(s["new_mrr"]) == [136, 267, 0, 0]
    assert math.isnan(s.loc[0, "mom_mrr_pct"])
    assert s.loc[1, "mom_mrr_pct"] == pytest.approx(403 / 136 - 1)
    assert s.loc[2, "mom_mrr_pct"] == pytest.approx(305 / 403 - 1)


def test_churn_uses_opening_cohort(tiny_con):
    s = C.monthly_series(tiny_con).set_index("month")
    mar, apr = s.loc["2024-03-01"], s.loc["2024-04-01"]
    assert (mar["opening_subs"], mar["churned_subs"], mar["churned_mrr"], mar["opening_mrr"]) == (5, 1, 98, 403)
    assert mar["gross_mrr_churn_rate"] == pytest.approx(98 / 403)
    assert (apr["opening_subs"], apr["churned_subs"], apr["sub_churn_rate"]) == (4, 1, pytest.approx(0.25))
    assert math.isnan(s.loc["2024-01-01", "sub_churn_rate"])      # empty opening base -> NaN, not 0 or inf


def test_date_filtering_of_series(tiny_con):
    s = C.monthly_series(tiny_con, Filters(start=date(2024, 2, 1), end=date(2024, 3, 31)))
    assert list(s["mrr"]) == [403, 305]
    assert s["month"].min() == pd.Timestamp("2024-02-01")


def test_categorical_filters(tiny_con):
    assert C.mrr_at(tiny_con, date(2024, 4, 30), Filters(industries=("DevTools",)))["mrr"] == 87     # A1 only (A4's sub ended)
    assert C.mrr_at(tiny_con, date(2024, 4, 30), Filters(countries=("US",)))["mrr"] == 286
    assert C.mrr_at(tiny_con, date(2024, 4, 30), Filters(plans=("Pro",)))["mrr"] == 49
    assert C.mrr_at(tiny_con, date(2024, 4, 30), Filters(industries=("FinTech",)))["mrr"] == 0


def test_no_double_counting_across_joins(tiny_con):
    """A1 has 2 subs, 2 tickets and 3 usage rows; naive three-way join would multiply MRR. Our mart must not."""
    naive = tiny_con.execute("""select sum(s.mrr_amount) from subscriptions s join support_tickets t using(account_id)
                                where s.is_paid and s.end_date is null""").fetchone()[0]
    assert naive != 286                                                    # proves the hazard exists in this data
    assert tiny_con.execute("select sum(mrr_at_snapshot) from account_features").fetchone()[0] == 286
    a1 = tiny_con.execute("select total_usage_count, n_tickets, avg_first_response_min from account_features where account_id='A1'").fetchone()
    assert a1 == (10, 2, 20.0)


def test_kpis_and_churn_rates(tiny_con):
    k = C.kpis(tiny_con)
    assert k["mrr"] == 286 and k["accounts"] == 4 and k["churned_accounts"] == 2
    assert k["account_churn_rate"] == 0.5 and k["accounts_with_churn_event"] == 3


def test_churn_by_segment_known_values(tiny_con):
    r = C.churn_by_segment(tiny_con, "industry")
    d = r.df.set_index("industry")
    assert d.loc["DevTools", "accounts"] == 2 and d.loc["DevTools", "churned"] == 1 and d.loc["DevTools", "churn_rate"] == 0.5
    assert d.loc["FinTech", "churn_rate"] == 1.0 and d.loc["EdTech", "churn_rate"] == 0.0
    ev = C.churn_by_segment(tiny_con, "industry", basis="event").df.set_index("industry")
    assert ev.loc["EdTech", "churned"] == 1                                # event basis counts A3
    with pytest.raises(ValueError):
        C.churn_by_segment(tiny_con, "mrr_amount; drop table accounts")    # dimension is whitelisted


def test_churn_reasons_exclude_duplicate_event(tiny_con):
    d = C.churn_reasons(tiny_con).df.set_index("reason")
    assert d["churn_events"].sum() == 3 and d.loc["pricing", "churn_events"] == 1


def test_cohort_retention_known(tiny_con):
    r = C.cohort_retention(tiny_con).df
    assert list(r["months_since_signup"]) == [1]                           # later months not fully observable
    assert r.loc[0, "retention"] == pytest.approx(0.75) and r.loc[0, "accounts"] == 4


def test_support_summary_known(tiny_con):
    d = C.support_summary(tiny_con).df.set_index("priority")
    assert d.loc["urgent", "avg_first_response_min"] == 60 and d.loc["high", "avg_resolution_hours"] == 24
    assert d["csat_responses"].sum() == 2                                    # one ticket has no CSAT


def test_mrr_definitions_differ(tiny_con):
    d = C.mrr_definitions(tiny_con).df
    assert d.iloc[0]["mrr"] == 286
    assert d.iloc[1]["mrr"] == 49 + 199                                      # most recent open sub per account: A1->S2 (49), A3->S4 (199)


def test_unavailable_metrics_are_labelled_not_computed():
    from src.metrics.definitions import METRICS
    for mid in ("cash_revenue", "customer_lifetime_value", "cac", "net_revenue_retention"):
        assert METRICS[mid].status == "unavailable"
    r = C.data_gaps()
    assert (r.df["status"] == "unavailable").sum() >= 4


def test_flag_spikes():
    s = pd.Series([10, 12, 11, 40, 12], index=list("abcde"))
    out = C.flag_spikes(s, window=3, ratio=2, min_value=15)
    assert list(out["period"]) == ["d"]
    assert C.flag_spikes(pd.Series([1, 1, 1, 5]), min_value=15).empty


def test_stats_helpers():
    lo, hi = wilson_interval(5, 10)
    assert 0.2 < lo < 0.5 < hi < 0.8 and wilson_interval(0, 0)[0] != wilson_interval(0, 0)[0]
    df = pd.DataFrame({"accounts": [100, 100], "churned": [10, 50]})
    assert chi_square_churn(df, "x")["p_value"] < 0.001
    assert chi_square_churn(pd.DataFrame({"accounts": [100, 100], "churned": [20, 20]}), "x")["p_value"] == pytest.approx(1.0)
    assert math.isnan(mann_whitney([1, 2], [3, 4, 5])["p_value"])            # too few observations
    assert "not statistically" in describe_p(0.5) and "significant" in describe_p(0.07) and "detectable" in describe_p(0.01)


def test_real_data_independent_validation(real_con):
    """Compare dashboard values with figures computed independently via pandas straight from the CSVs."""
    from tests.conftest import ROOT
    subs = pd.read_csv(ROOT / "data/raw/ravenstack_subscriptions.csv", parse_dates=["start_date", "end_date"])
    acc = pd.read_csv(ROOT / "data/raw/ravenstack_accounts.csv")
    open_paid = subs[(~subs["is_trial"]) & subs["end_date"].isna()]
    assert C.mrr_at(real_con)["mrr"] == pytest.approx(open_paid["mrr_amount"].sum())
    assert C.kpis(real_con)["churned_accounts"] == int(acc["churn_flag"].sum())
    mar = pd.Timestamp("2024-06-30")
    active = subs[(~subs["is_trial"]) & (subs["start_date"] <= mar) & (subs["end_date"].isna() | (subs["end_date"] > mar))]
    assert C.mrr_at(real_con, date(2024, 6, 30))["mrr"] == pytest.approx(active["mrr_amount"].sum())
    by_plan = active.groupby("plan_tier")["mrr_amount"].sum()
    rb = C.revenue_breakdown(real_con, "plan_tier", Filters(end=date(2024, 6, 30))).df.set_index("plan_tier")["mrr"]
    pd.testing.assert_series_equal(rb.sort_index(), by_plan.sort_index(), check_names=False, check_dtype=False)
    assert C.mrr_at(real_con)["mrr"] == C.monthly_series(real_con).iloc[-1]["mrr"]


def test_default_date_range_excludes_no_accounts(tiny_con):
    """Regression: the dashboard's default range once started at the first subscription and silently dropped accounts."""
    f = Filters(start=C.data_start(tiny_con), end=C.snapshot_date(tiny_con))
    assert C.kpis(tiny_con, f)["accounts"] == 4 == C.kpis(tiny_con)["accounts"]


def test_default_date_range_excludes_no_accounts_real(real_con):
    f = Filters(start=C.data_start(real_con), end=C.snapshot_date(real_con))
    k, k0 = C.kpis(real_con, f), C.kpis(real_con)
    assert k["accounts"] == k0["accounts"] == 500 and k["churned_accounts"] == k0["churned_accounts"] == 110
