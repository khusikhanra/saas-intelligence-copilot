"""Result verification: sanity checks run on every result before it is explained to the user."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_RATE = re.compile(r"(rate|share|retention|pct|percent|ratio)", re.I)
_COUNT = re.compile(r"(^n_|count|accounts|tickets|events|subscriptions|churned|seats)", re.I)


@dataclass
class Verification:
    ok: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def verify_result(df: pd.DataFrame, truncated: bool = False, allow_empty: bool = False) -> Verification:
    v = Verification()
    if df is None:
        v.ok = False
        v.errors.append("No result was produced.")
        return v
    if df.empty:
        if not allow_empty:
            v.ok = False
            v.errors.append("The query returned no rows, so there is nothing to report. The filters may be too narrow or the question may not match the data.")
        return v
    if len(df.columns) != len(set(df.columns)):
        v.warnings.append("Result has duplicate column names.")
    for c in df.columns:
        col = df[c]
        if col.isna().all():
            v.warnings.append(f"Column '{c}' is entirely empty.")
        if pd.api.types.is_numeric_dtype(col) and not pd.api.types.is_bool_dtype(col):
            if np.isinf(col.astype(float)).any():
                v.ok = False
                v.errors.append(f"Column '{c}' contains infinite values (division by zero).")
                continue
            if _RATE.search(str(c)) and "pct" not in str(c).lower() and "mom" not in str(c).lower():
                bad = col.dropna()[(col.dropna() < -1e-9) | (col.dropna() > 1 + 1e-9)]
                if len(bad) and "x_" not in str(c):
                    v.ok = False
                    v.errors.append(f"Column '{c}' looks like a rate/share but has values outside 0-1 (e.g. {bad.iloc[0]:.3g}); possible double counting.")
            elif _COUNT.search(str(c)) and (col.dropna() < 0).any():
                v.ok = False
                v.errors.append(f"Column '{c}' has negative counts.")
    # a grouped result should not repeat its first (key) column
    first = df.columns[0]
    if not pd.api.types.is_numeric_dtype(df[first]) and df[first].duplicated().any() and len(df.columns) > 1:
        v.warnings.append(f"Values of '{first}' repeat across rows; if this was meant to be a grouped summary, a join may have duplicated rows.")
    if truncated:
        v.warnings.append("Result was truncated to the row limit; totals computed from it are incomplete.")
    return v
