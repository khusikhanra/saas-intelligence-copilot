"""Offline (no API key) question router: keyword rules -> TRUSTED analyses. Real computation, limited coverage."""
from __future__ import annotations

import re

from src.ai.planner import Plan


def _has(q: str, *words: str) -> bool:
    return any(re.search(w, q) for w in words)


def route_question(question: str) -> Plan:
    q = question.lower()
    mk = lambda aid, **p: Plan("analysis", analysis_id=aid, params=p, source="rules")
    if _has(q, r"\bltv\b", r"lifetime value", r"\bclv\b", r"\bcac\b", r"acquisition cost", r"cash", r"net revenue retention", r"\bnrr\b", r"trial conversion"):
        return Plan("analysis", analysis_id="data_gaps", source="rules", message="That metric is not computable from this dataset; here is what is missing.")
    if _has(q, r"additional data", r"what data", r"missing data", r"data (would|could) help", r"data gaps?", r"unavailable"):
        return mk("data_gaps")
    if _has(q, r"what (should|can) (we|management)", r"recommend", r"action", r"insight", r"summar"):
        return mk("business_insights")
    if _has(q, r"unusual", r"anomal", r"spike", r"surpris", r"strange", r"outlier"):
        return mk("unusual_changes")
    if _has(q, r"high[- ]value", r"at[- ]risk", r"watch ?list", r"biggest accounts"):
        return mk("high_value_at_risk")
    if _has(q, r"cohort", r"retention"):
        return mk("cohort_retention")
    if _has(q, r"reason"):
        return mk("churn_reasons")
    if _has(q, r"support", r"response time", r"resolution", r"ticket", r"csat") and _has(q, r"churn", r"associat", r"relat", r"affect"):
        return mk("support_vs_churn")
    if _has(q, r"usage", r"feature", r"engagement") and _has(q, r"churn", r"associat", r"relat"):
        return mk("usage_vs_churn")
    if _has(q, r"support", r"ticket", r"resolution", r"csat"):
        return mk("support_summary")
    if _has(q, r"which features", r"most used", r"feature usage", r"popular feature"):
        return mk("feature_usage_summary")
    if _has(q, r"churn") and _has(q, r"month", r"trend", r"over time", r"increasing", r"rising"):
        return mk("monthly_churn")
    if _has(q, r"churn"):
        dim = "industry"
        for pat, d in ((r"countr|region|geograph", "country"), (r"channel|referral|acquisition|source", "referral_source"), (r"plan|tier", "plan_tier"),
                       (r"seat|size", "seats_band"), (r"industr|vertical|sector", "industry")):
            if re.search(pat, q):
                dim = d
                break
        return mk("churn_by_segment", dimension=dim, basis="event" if _has(q, r"event") else "flag")
    if _has(q, r"month[- ]over[- ]month", r"\bmom\b", r"growth", r"trend", r"changed", r"over time"):
        return mk("mrr_trend")
    if _has(q, r"alternative", r"how is mrr (defined|calculated)", r"different definitions?", r"compare.*definitions?"):
        return mk("mrr_definitions")
    if _has(q, r"active") and _has(q, r"subscription") and _has(q, r"plan"):
        return mk("active_subs_by_plan")
    if _has(q, r"revenue", r"mrr", r"arr") and _has(q, r"plan|tier|countr|region|industr|channel|referral|source"):
        dim = "plan_tier"
        for pat, d in ((r"countr|region", "country"), (r"industr", "industry"), (r"channel|referral|source", "referral_source")):
            if re.search(pat, q):
                dim = d
                break
        return mk("revenue_breakdown", dimension=dim)
    if _has(q, r"\bmrr\b", r"\barr\b", r"recurring revenue", r"current revenue", r"active (paying )?(accounts|customers)"):
        return mk("current_mrr")
    if _has(q, r"plan") and _has(q, r"subscription"):
        return mk("active_subs_by_plan")
    return Plan("unsupported", source="rules",
                message="I couldn't map that to a built-in analysis without an AI key. Try one of the example questions, or set OPENAI_API_KEY to enable free-form questions.")


_DIM_WORDS = (("country", r"countr|region|geograph"), ("referral_source", r"channel|referral|acquisition|source"),
              ("plan_tier", r"\bplan|tier"), ("seats_band", r"seat|size"), ("industry", r"industr|vertical|sector"))


def route_followup(question: str, history: list[dict]) -> Plan | None:
    """Offline follow-ups: a short message naming only a new dimension re-runs the previous segment analysis."""
    if not history:
        return None
    last = history[-1]
    q = question.lower()
    if len(q.split()) > 10 or last.get("method_id") not in ("churn_by_segment", "revenue_breakdown"):
        return None
    for dim, pat in _DIM_WORDS:
        if re.search(pat, q):
            if last["method_id"] == "revenue_breakdown" and dim == "seats_band":
                return None
            params = dict(last.get("params") or {})
            params["dimension"] = dim
            return Plan("analysis", analysis_id=last["method_id"], params=params, source="rules",
                        assumptions=[f"Interpreted as a follow-up: same analysis as before, grouped by {dim}."])
    return None
