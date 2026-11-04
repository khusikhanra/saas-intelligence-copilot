"""Orchestrates: input validation -> plan -> trusted analysis or validated SQL -> verification -> grounded explanation."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

from src.ai.client import LLMClient, LLMError
from src.ai.explain import deterministic_sql_summary, llm_explain
from src.ai.planner import Plan, PlanError, plan_with_llm
from src.ai.router import route_followup, route_question
from src.analytics.catalog import AnalysisParamError, run_analysis
from src.analytics.db import QueryError, run_query
from src.analytics.dq_warnings import format_issue, relevant_issues
from src.analytics.verification import verify_result
from src.metrics.calculations import AnalysisResult, Filters, filter_options
from src.security.input_validation import InputError, clean_question
from src.security.logging_utils import log_event, question_fingerprint
from src.security.sql_guard import SQLValidationError
from src.ui.charts import auto_chart_spec

EXAMPLE_QUESTIONS = [
    "What is the current MRR under the documented metric definition?",
    "How has MRR changed month over month?",
    "Which plans have the most active subscriptions?",
    "Which industries have higher observed churn rates?",
    "What are the most common recorded churn reasons?",
    "Is support response time associated with churn?",
    "Which cohorts show the strongest retention?",
    "What unusual changes appear in the available data?",
    "What additional data would help explain churn?",
    "Which high-value accounts are at risk?",
    "What should management do based on this data?",
]


@dataclass
class CopilotResponse:
    status: str                      # ok | unsupported | clarify | error
    mode: str                        # llm | offline
    answer: str = ""
    key_points: list = field(default_factory=list)
    df: pd.DataFrame | None = None
    chart_spec: dict | None = None
    method_kind: str = ""            # analysis | sql
    method_params: dict = field(default_factory=dict)
    method_id: str = ""
    method_text: str = ""
    caveats: list = field(default_factory=list)
    dq_warnings: list = field(default_factory=list)
    verification_warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)
    sections: dict | None = None
    title: str = ""
    elapsed_ms: int = 0

    def history_entry(self, question: str) -> dict:
        """Compact context for follow-ups. Prior answers are passed as UNVERIFIED reference text only."""
        return {"question": question, "method_kind": self.method_kind, "method_id": self.method_id,
                "columns": list(self.df.columns) if self.df is not None else [], "headline": self.answer[:300],
                "params": self.method_params}


class Copilot:
    def __init__(self, con, llm: LLMClient | None = None):
        self.con = con
        self.llm = llm
        self._opts = None

    @property
    def mode(self) -> str:
        return "llm" if self.llm is not None else "offline"

    def _filter_options(self) -> dict:
        if self._opts is None:
            self._opts = filter_options(self.con)
        return self._opts

    # ---------------------------------------------------------------------------------------------
    def ask(self, question: str, history: list[dict] | None = None, base_filters: Filters | None = None) -> CopilotResponse:
        t0 = time.perf_counter()
        history = history or []
        try:
            question = clean_question(question)
        except InputError as exc:
            return CopilotResponse("error", self.mode, answer=str(exc))
        notes: list[str] = []
        plan: Plan | None = None
        if self.llm is not None:
            try:
                plan = plan_with_llm(self.llm, question, history, self._filter_options())
            except (LLMError, PlanError) as exc:
                notes.append(f"AI planning unavailable ({exc}); used built-in routing instead.")
        if plan is None:
            plan = route_followup(question, history) or route_question(question)
        resp = self._execute(question, plan, history, base_filters or Filters(), notes)
        resp.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log_event("copilot_answer", fp=question_fingerprint(question), status=resp.status, mode=resp.mode, kind=resp.method_kind,
                  method=resp.method_id, rows=0 if resp.df is None else len(resp.df), ms=resp.elapsed_ms)
        return resp

    # ---------------------------------------------------------------------------------------------
    def _execute(self, question, plan: Plan, history, base_filters: Filters, notes) -> CopilotResponse:
        r = CopilotResponse("ok", self.mode, notes=list(notes), assumptions=list(plan.assumptions))
        if plan.intent in ("unsupported", "clarify"):
            r.status = plan.intent
            r.answer = plan.message or "I can't answer that from this dataset."
            return r
        filters = base_filters
        if plan.filters:
            filters = Filters(start=base_filters.start, end=base_filters.end, industries=plan.filters.get("industries", base_filters.industries),
                              countries=plan.filters.get("countries", base_filters.countries),
                              referral_sources=plan.filters.get("referral_sources", base_filters.referral_sources),
                              plans=plan.filters.get("plans", base_filters.plans))
        if plan.intent == "analysis":
            if plan.message:
                r.notes.append(plan.message)
            return self._run_analysis(r, question, plan, filters)
        return self._run_sql(r, question, plan, history, filters)

    def _run_analysis(self, r: CopilotResponse, question: str, plan: Plan, filters: Filters) -> CopilotResponse:
        try:
            res: AnalysisResult = run_analysis(self.con, plan.analysis_id, plan.params, filters)
        except AnalysisParamError as exc:
            r.status, r.answer = "error", f"The analysis request was invalid: {exc}"
            return r
        except Exception as exc:  # noqa: BLE001
            log_event("analysis_failed", id=plan.analysis_id, kind=type(exc).__name__)
            r.status, r.answer = "error", "The analysis failed unexpectedly. Please try rephrasing the question."
            return r
        ver = verify_result(res.df, allow_empty=True)
        if not ver.ok:
            r.status, r.answer = "error", "Result failed verification and was withheld: " + " ".join(ver.errors)
            return r
        r.method_kind, r.method_id, r.method_text, r.title = "analysis", res.id, res.method, res.title
        r.method_params = dict(plan.params)
        r.df, r.chart_spec = res.df, res.chart
        r.caveats = list(res.caveats)
        r.verification_warnings = ver.warnings
        r.sections = res.stats.get("sections") if res.id == "business_insights" else None
        if filters != Filters():
            r.notes.append(f"Filters applied: {filters.describe()}.")
        r.answer, r.key_points = res.headline, list(res.facts)
        if res.id != "business_insights" and not res.df.empty:
            exp_, note = llm_explain(self.llm, question, res.df, res.headline, res.facts, res.caveats)
            if exp_:
                r.answer, r.key_points = exp_["answer"], exp_["key_points"] or r.key_points
            if note:
                r.notes.append(note)
        r.dq_warnings = [format_issue(i) for i in relevant_issues(res.tables, res.metric_ids)]
        return r

    def _run_sql(self, r: CopilotResponse, question: str, plan: Plan, history, filters: Filters) -> CopilotResponse:
        sql, result, last_err = plan.sql, None, None
        for attempt in range(2):
            try:
                result = run_query(self.con, sql)
                break
            except (SQLValidationError, QueryError) as exc:
                last_err = str(exc)
                if attempt == 0 and self.llm is not None:
                    try:
                        retry = plan_with_llm(self.llm, question, history, self._filter_options(), error_feedback=last_err)
                    except (LLMError, PlanError):
                        break
                    if retry.intent == "analysis":
                        return self._run_analysis(r, question, retry, filters)
                    if retry.intent != "sql":
                        r.status, r.answer = retry.intent, retry.message or last_err
                        return r
                    sql = retry.sql
                    r.notes.append("The first query was rejected or failed, so it was regenerated once.")
                    continue
                break
        if result is None:
            r.status = "error"
            r.answer = f"I couldn't run a safe query for that question. {last_err or ''}".strip()
            return r
        ver = verify_result(result.df, truncated=result.truncated)
        r.method_kind, r.method_id, r.method_text, r.title = "sql", "generated_sql", result.sql, "Custom query"
        r.df = result.df
        if not ver.ok:
            r.status, r.answer = "error", "Result failed verification and was withheld: " + " ".join(ver.errors)
            return r
        r.chart_spec = auto_chart_spec(result.df)
        r.verification_warnings = ver.warnings + result.warnings
        summary, pts = deterministic_sql_summary(result.df, result.truncated)
        r.answer, r.key_points = summary, pts
        r.caveats = ["This answer comes from a generated query, not a documented metric function. Check the SQL against the Methodology page definitions.",
                     "Associations are not causes."]
        exp_, note = llm_explain(self.llm, question, result.df, summary, pts, r.caveats)
        if exp_:
            r.answer, r.key_points = exp_["answer"], exp_["key_points"] or pts
        if note:
            r.notes.append(note)
        r.dq_warnings = [format_issue(i) for i in relevant_issues(result.tables, [])]
        return r
