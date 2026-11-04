"""Numeric grounding check: every number in an AI-written explanation must be traceable to the computed result."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

_NUM = re.compile(r"(?<![\w.])-?\$?\d[\d,]*\.?\d*%?")


def _numbers(text: str) -> list[tuple[float, int, bool]]:
    out = []
    for m in _NUM.finditer(text or ""):
        tok = m.group(0)
        is_pct = tok.endswith("%")
        clean = tok.replace("$", "").replace(",", "").rstrip("%")
        if clean in ("", "-", "."):
            continue
        try:
            val = float(clean)
        except ValueError:
            continue
        decimals = len(clean.split(".")[1]) if "." in clean else 0
        out.append((val, decimals, is_pct))
    return out


def allowed_values(df: pd.DataFrame | None, extra_text: str = "") -> list[float]:
    vals: list[float] = []
    if df is not None:
        for c in df.columns:
            if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]):
                vals += [float(x) for x in df[c].dropna().tolist() if np.isfinite(x)]
        vals.append(float(len(df)))
        # year / month tokens from date-like columns
        for c in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                s = df[c].dropna()
                vals += [float(x) for x in s.dt.year.unique()] + [float(x) for x in s.dt.month.unique()] + [float(x) for x in s.dt.day.unique()]
    for v, _, _ in _numbers(extra_text):
        vals.append(v)
    return vals


def _matches(val: float, decimals: int, is_pct: bool, allowed: list[float]) -> bool:
    """A written number is grounded if it equals an allowed value after rounding to the precision it was written at.
    Percent tokens ('31.0%') match a fraction (0.31) scaled by 100, or a value already stored in percent units.
    Plain tokens match a stored value directly, or a stored fraction written as a percentage without the sign."""
    tol = 0.5 * 10 ** (-decimals) + 1e-9          # tolerance in the units the number was WRITTEN in
    for a in allowed:
        if is_pct:
            if abs(a * 100.0 - val) <= tol or abs(a - val) <= tol:
                return True
        else:
            if abs(a - val) <= tol:
                return True
            if 0.0 <= abs(a) <= 1.0 and abs(a * 100.0 - val) <= tol:
                return True
    return False


def ungrounded_numbers(answer_text: str, df: pd.DataFrame | None, context_text: str = "") -> list[str]:
    """Return numbers in answer_text that cannot be matched to the data/context (small integers 0-10 are ignored)."""
    allowed = allowed_values(df, context_text)
    bad = []
    for val, decimals, is_pct in _numbers(answer_text):
        if float(val).is_integer() and 0 <= abs(val) <= 10 and decimals == 0 and not is_pct:
            continue
        if not _matches(val, decimals, is_pct, allowed):
            bad.append(f"{val:g}{'%' if is_pct else ''}")
    return bad
