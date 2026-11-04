"""Small statistical helpers (kept dependency-light and unit-tested)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats as sps


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n <= 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def chi_square_churn(df: pd.DataFrame, group_col: str, churn_col: str = "churned", n_col: str = "accounts") -> dict:
    """Chi-square test of independence between segment and churn. Returns p-value and expected-count warning."""
    if len(df) < 2:
        return {"p_value": float("nan"), "chi2": float("nan"), "dof": 0, "small_expected": False}
    table = np.array([df[churn_col].to_numpy(), (df[n_col] - df[churn_col]).to_numpy()]).T
    if (table.sum(axis=0) == 0).any():
        return {"p_value": float("nan"), "chi2": float("nan"), "dof": 0, "small_expected": False}
    chi2, p, dof, expected = sps.chi2_contingency(table, correction=False)
    return {"p_value": float(p), "chi2": float(chi2), "dof": int(dof), "small_expected": bool((expected < 5).any())}


def mann_whitney(a, b) -> dict:
    a, b = pd.Series(a).dropna(), pd.Series(b).dropna()
    if len(a) < 3 or len(b) < 3:
        return {"p_value": float("nan"), "n_a": len(a), "n_b": len(b)}
    res = sps.mannwhitneyu(a, b, alternative="two-sided")
    return {"p_value": float(res.pvalue), "n_a": len(a), "n_b": len(b)}


def describe_p(p: float, alpha: float = 0.05) -> str:
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "significance could not be tested"
    if p < alpha:
        return f"statistically detectable at the 5% level (p = {p:.3f}; association only, not causation)"
    if p < 0.10:
        return f"suggestive but not statistically significant at the 5% level (p = {p:.3f}); more data would be needed"
    return f"not statistically distinguishable from chance (p = {p:.3f})"
