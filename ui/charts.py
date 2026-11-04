"""Plotly chart builders. All colours and fonts come from the active theme (src/ui/theme.py), so charts follow theme switches."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from src.ui import theme as T


def _tokens():
    th, ac = T.current()
    t, acc, _ = T.resolve(th, None if ac == T.DEFAULT_ACCENT else ac)
    return th, ac, t, acc


def _style(fig: go.Figure, height: int = 340, has_title: bool = False) -> go.Figure:
    th, ac, t, acc = _tokens()
    layout = T.plotly_layout(th, ac)
    if not has_title:
        layout["margin"] = {"l": 8, "r": 8, "t": 12, "b": 8}
        layout.pop("title", None)
    fig.update_layout(**layout, height=height, hovermode="x unified" if fig.data and fig.data[0].type in ("scatter",) else "closest")
    fig.update_xaxes(showgrid=False, ticks="outside", tickcolor=t.border, automargin=True)
    fig.update_yaxes(zeroline=False, automargin=True)
    return fig


def auto_chart_spec(df: pd.DataFrame) -> dict | None:
    """Choose a chart for an arbitrary result: line for time, bar for categories; None if no sensible chart."""
    if df is None or df.empty or len(df.columns) < 2 or len(df) > 60:
        return None
    first = df.columns[0]
    nums = [c for c in df.columns[1:] if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    if not nums:
        return None
    y = nums[0]
    if pd.api.types.is_datetime64_any_dtype(df[first]) or "month" in str(first).lower() or "date" in str(first).lower():
        return {"kind": "line", "x": first, "y": y} if len(df) > 1 else None
    if not pd.api.types.is_numeric_dtype(df[first]) and df[first].is_unique:
        return {"kind": "bar", "x": first, "y": y}
    return None


def _axis_format(fig: go.Figure, y: str) -> None:
    name = str(y).lower()
    if any(k in name for k in ("rate", "share", "retention")):
        fig.update_yaxes(tickformat=".0%", hoverformat=".1%")
    elif any(k in name for k in ("mrr", "revenue", "value", "arr")):
        fig.update_yaxes(tickprefix="$", tickformat="~s", hoverformat="$,.0f")
    else:
        fig.update_yaxes(tickformat="~s", hoverformat=",.4~g")


def make_chart(df: pd.DataFrame, spec: dict | None, title: str = "", height: int = 340):
    if spec is None or df is None or df.empty:
        return None
    th, ac, t, acc = _tokens()
    kind, x, y = spec.get("kind"), spec.get("x"), spec.get("y")
    if kind == "heatmap":
        piv = df.pivot(index=spec["y"], columns=spec["x"], values=spec["z"]).sort_index()
        fig = go.Figure(go.Heatmap(z=piv.values, x=piv.columns, y=piv.index, zmin=0, zmax=1, xgap=2, ygap=2,
                                   colorscale=[[0, t.surface2], [1, acc]], hovertemplate="cohort %{y}<br>month %{x}: %{z:.1%}<extra></extra>",
                                   colorbar={"tickformat": ".0%", "outlinewidth": 0, "tickfont": {"color": t.muted}}))
        _style(fig, height, bool(title))
        fig.update_layout(xaxis_title="Months since signup", yaxis_title=None)
        fig.update_xaxes(dtick=1)
        return fig
    if x not in df.columns or y not in df.columns:
        return None
    if kind == "line":
        fig = px.line(df, x=x, y=y, markers=True, title=title or None)
        fig.update_traces(line={"width": 2.6, "shape": "spline", "smoothing": 0.35, "color": acc}, marker={"size": 5, "color": acc}, fill="tozeroy", fillcolor=T.rgba(acc, 0.12))
    else:
        fig = px.bar(df, x=x, y=y, title=title or None)
        fig.update_traces(marker={"color": acc, "cornerradius": 6})
    _style(fig, height, bool(title))
    _axis_format(fig, y)
    fig.update_layout(xaxis_title=None, yaxis_title=None)
    return fig


def mrr_waterfall_bars(series: pd.DataFrame):
    th, ac, t, acc = _tokens()
    fig = go.Figure()
    fig.add_bar(x=series["month"], y=series["new_mrr"], name="New MRR", marker={"color": t.success, "cornerradius": 4})
    fig.add_bar(x=series["month"], y=-series["churned_mrr"], name="Churned MRR", marker={"color": t.danger, "cornerradius": 4})
    fig.add_scatter(x=series["month"], y=series["net_new_mrr"], name="Net new MRR", mode="lines", line={"color": acc, "width": 2.4})
    fig.update_layout(barmode="relative")
    _style(fig, 340)
    fig.update_layout(legend={"orientation": "h", "y": 1.12, "x": 0, "font": {"color": t.muted}}, hovermode="x unified")
    fig.update_yaxes(tickprefix="$", tickformat="~s", hoverformat="$,.0f")
    return fig


def churn_bar_with_ci(df: pd.DataFrame, x: str, title: str = ""):
    th, ac, t, acc = _tokens()
    err_hi = (df["ci_high"] - df["churn_rate"]).clip(lower=0)
    err_lo = (df["churn_rate"] - df["ci_low"]).clip(lower=0)
    fig = go.Figure(go.Bar(x=df[x], y=df["churn_rate"], marker={"color": acc, "cornerradius": 6},
                           error_y={"type": "data", "symmetric": False, "array": err_hi, "arrayminus": err_lo, "color": t.muted, "thickness": 1.4, "width": 4},
                           customdata=df[["accounts", "churned"]].values,
                           hovertemplate="%{x}<br>churn %{y:.1%}<br>%{customdata[1]} of %{customdata[0]} accounts<extra></extra>"))
    _style(fig, 340)
    fig.update_yaxes(tickformat=".0%", rangemode="tozero")
    return fig
