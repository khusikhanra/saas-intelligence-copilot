"""Question -> validated plan. The LLM may only choose a catalog analysis or write SQL; nothing is executed here."""
from __future__ import annotations

from dataclasses import dataclass, field

from src.ai.client import LLMClient
from src.ai.prompts import planner_system
from src.analytics.catalog import CATALOG

INTENTS = {"analysis", "sql", "unsupported", "clarify"}
FILTER_KEYS = ("industries", "countries", "referral_sources", "plans")


class PlanError(ValueError):
    pass


@dataclass
class Plan:
    intent: str
    analysis_id: str | None = None
    params: dict = field(default_factory=dict)
    sql: str | None = None
    filters: dict = field(default_factory=dict)
    message: str = ""
    assumptions: list = field(default_factory=list)
    source: str = "llm"  # "llm" or "rules"


def parse_plan(data: dict, filter_options: dict | None = None) -> Plan:
    intent = data.get("intent")
    if intent not in INTENTS:
        raise PlanError("Planner returned an unknown intent.")
    plan = Plan(intent=intent, message=str(data.get("message") or "")[:500],
                assumptions=[str(a)[:200] for a in (data.get("assumptions") or [])][:5])
    if intent == "analysis":
        aid = data.get("analysis_id")
        if aid not in CATALOG:
            raise PlanError(f"Planner chose an unknown analysis: {aid!r}.")
        plan.analysis_id = aid
        params = data.get("params") or {}
        plan.params = params if isinstance(params, dict) else {}
    elif intent == "sql":
        sql = data.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise PlanError("Planner chose SQL but gave none.")
        plan.sql = sql
    raw_f = data.get("filters") or {}
    if isinstance(raw_f, dict) and filter_options:
        opt_map = {"industries": "industry", "countries": "country", "referral_sources": "referral_source", "plans": "plan_tier"}
        for k in FILTER_KEYS:
            vals = raw_f.get(k) or []
            if isinstance(vals, list):
                valid = [v for v in vals if v in filter_options.get(opt_map[k], [])]
                if valid:
                    plan.filters[k] = tuple(valid)
    return plan


def _history_text(history: list[dict]) -> str:
    if not history:
        return "(none)"
    lines = []
    for h in history[-4:]:
        lines.append(f"- Q: {h.get('question', '')[:200]} | method: {h.get('method_kind', '?')} {h.get('method_id', '')} | columns: {', '.join(h.get('columns', [])[:8])} "
                     f"| prior answer (UNVERIFIED, for reference only): {h.get('headline', '')[:200]}")
    return "\n".join(lines)


def plan_with_llm(llm: LLMClient, question: str, history: list[dict], filter_options: dict | None = None, error_feedback: str | None = None) -> Plan:
    user = f"Prior turns (use only to resolve references like 'that' or 'those'):\n{_history_text(history)}\n\nUser question: {question}"
    if error_feedback:
        user += f"\n\nYour previous SQL was rejected or failed: {error_feedback}\nReturn a corrected plan (prefer a catalog analysis if possible)."
    return parse_plan(llm.chat_json(planner_system(), user), filter_options)
