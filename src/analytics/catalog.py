"""Catalog of trusted analyses the copilot can call (planner picks one; params are validated here)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from src.metrics import calculations as C
from src.metrics.calculations import AnalysisResult, Filters


@dataclass
class AnalysisSpec:
    id: str
    description: str
    func: Callable
    params: dict = field(default_factory=dict)  # name -> list of allowed values


def _dim(allowed):
    return {"dimension": list(allowed)}


CATALOG: dict[str, AnalysisSpec] = {s.id: s for s in [
    AnalysisSpec("current_mrr", "Current MRR/ARR, active paying accounts and month-over-month change.", lambda con, f, **k: C.current_mrr(con, f)),
    AnalysisSpec("mrr_definitions", "MRR under the selected definition and two alternatives (shows how definition-dependent MRR is).", lambda con, f, **k: C.mrr_definitions(con)),
    AnalysisSpec("mrr_trend", "Monthly MRR with month-over-month growth, new MRR and churned MRR.", lambda con, f, **k: C.mrr_trend(con, f)),
    AnalysisSpec("active_subs_by_plan", "Active paid subscriptions (and MRR) by subscription plan.", lambda con, f, **k: C.active_subs_by_plan(con, f)),
    AnalysisSpec("revenue_breakdown", "MRR by a dimension.", lambda con, f, dimension="plan_tier", **k: C.revenue_breakdown(con, dimension, f), _dim(C.REVENUE_DIMENSIONS)),
    AnalysisSpec("churn_by_segment", "Observed account churn rate by a segment, with confidence intervals and a significance test.",
                 lambda con, f, dimension="industry", basis="flag", **k: C.churn_by_segment(con, dimension, f, basis),
                 {"dimension": list(C.ACCOUNT_DIMENSIONS), "basis": ["flag", "event"]}),
    AnalysisSpec("churn_reasons", "Most common recorded churn reasons.", lambda con, f, **k: C.churn_reasons(con, f)),
    AnalysisSpec("monthly_churn", "Monthly subscription churn and gross MRR churn rates.", lambda con, f, **k: C.monthly_churn(con, f)),
    AnalysisSpec("cohort_retention", "Retention by signup-quarter cohort.", lambda con, f, **k: C.cohort_retention(con, f)),
    AnalysisSpec("support_vs_churn", "Association between support metrics and churn.", lambda con, f, metric="avg_first_response_min", **k: C.support_vs_churn(con, metric, f),
                 {"metric": list(C.SUPPORT_METRICS)}),
    AnalysisSpec("usage_vs_churn", "Association between feature usage/engagement and churn.", lambda con, f, metric="total_usage_count", **k: C.usage_vs_churn(con, metric, f),
                 {"metric": list(C.USAGE_METRICS)}),
    AnalysisSpec("support_summary", "Ticket volume, response/resolution time, CSAT by priority.", lambda con, f, **k: C.support_summary(con, f)),
    AnalysisSpec("feature_usage_summary", "Most used features.", lambda con, f, **k: C.feature_usage_summary(con, f)),
    AnalysisSpec("unusual_changes", "Spikes and unusual movements in subscriptions, churn and MRR.", lambda con, f, **k: C.unusual_changes(con, f)),
    AnalysisSpec("high_value_at_risk", "Watchlist of high-MRR accounts with recent churn events (rule-based, not predictive).", lambda con, f, **k: C.high_value_at_risk(con, f)),
    AnalysisSpec("business_insights", "Evidence-based summary: observed facts, metrics, possible explanations, recommendations, limitations. Use for 'what should management do' questions.",
                 lambda con, f, **k: __import__("src.analytics.insights", fromlist=["x"]).insights_result(con, f)),
    AnalysisSpec("data_gaps", "Metrics that are unavailable/approximate and what additional data would help.", lambda con, f, **k: C.data_gaps(con, f)),
]}


class AnalysisParamError(ValueError):
    pass


def run_analysis(con, analysis_id: str, params: dict | None = None, filters: Filters | None = None) -> AnalysisResult:
    spec = CATALOG.get(analysis_id)
    if spec is None:
        raise AnalysisParamError(f"Unknown analysis '{analysis_id}'.")
    params = dict(params or {})
    clean = {}
    for k, v in params.items():
        if k not in spec.params:
            continue  # ignore unknown params instead of passing them through
        if v not in spec.params[k]:
            raise AnalysisParamError(f"Invalid value {v!r} for '{k}'. Allowed: {spec.params[k]}")
        clean[k] = v
    return spec.func(con, filters or Filters(), **clean)


def catalog_prompt() -> str:
    lines = []
    for s in CATALOG.values():
        ps = "; ".join(f"{k} in {v}" for k, v in s.params.items())
        lines.append(f"- {s.id}: {s.description}" + (f" Params: {ps}." if ps else ""))
    return "\n".join(lines)
