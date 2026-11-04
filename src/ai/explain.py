"""Grounded explanations. LLM prose is accepted only if every number in it can be traced to the computed result."""
from __future__ import annotations

import pandas as pd

from src.ai.client import LLMClient, LLMError
from src.ai.grounding import ungrounded_numbers
from src.ai.prompts import EXPLAIN_SYSTEM
from src.security.logging_utils import log_event


def deterministic_sql_summary(df: pd.DataFrame, truncated: bool = False) -> tuple[str, list]:
    n = len(df)
    pts = []
    if n:
        first = df.iloc[0]
        pts.append("First row: " + ", ".join(f"{c} = {first[c]}" for c in df.columns[:6]))
    if truncated:
        pts.append("Rows were truncated at the row limit.")
    return f"The query returned {n:,} row{'s' if n != 1 else ''} and {len(df.columns)} column{'s' if len(df.columns) != 1 else ''}.", pts


def llm_explain(llm: LLMClient | None, question: str, df: pd.DataFrame, headline: str, facts: list, caveats: list) -> tuple[dict | None, str | None]:
    """Returns (explanation or None, note). None means: use the deterministic text instead."""
    if llm is None:
        return None, None
    payload = (f"Question: {question}\n\nComputed headline: {headline}\nFacts: {facts}\nCaveats: {caveats}\n\n"
               f"Result table (first 30 rows, CSV):\n{df.head(30).to_csv(index=False)}")
    try:
        out = llm.chat_json(EXPLAIN_SYSTEM, payload, max_tokens=500)
    except LLMError as exc:
        return None, f"AI explanation unavailable ({exc}); showing the computed summary."
    answer = str(out.get("answer", "")).strip()
    points = [str(p).strip() for p in (out.get("key_points") or []) if str(p).strip()][:4]
    if not answer:
        return None, "AI explanation was empty; showing the computed summary."
    text = answer + " " + " ".join(points)
    bad = ungrounded_numbers(text, df, context_text=f"{headline} {' '.join(map(str, facts))} {question}")
    if bad:
        log_event("explanation_rejected", reason="ungrounded_numbers", n=len(bad))
        return None, "The AI-written explanation contained numbers that could not be verified against the result (" + ", ".join(bad[:4]) + "), so the computed summary is shown instead."
    return {"answer": answer, "key_points": points}, None
