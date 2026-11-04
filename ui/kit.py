"""Reusable presentation components. Presentation only: no analytics logic, no database access.

Every dynamic string is passed through html.escape before it reaches unsafe_allow_html, so data values (account names,
reason codes, SQL text, AI output) cannot inject markup.
"""
from __future__ import annotations

import html
import math
import re
from contextlib import contextmanager
from typing import Iterable

import pandas as pd
import streamlit as st


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _html(markup: str) -> None:
    # collapse whitespace between tags: markdown treats indented / blank-line-separated HTML as code
    st.markdown(re.sub(r">\s+<", "><", markup.strip()), unsafe_allow_html=True)


# ------------------------------------------------------------------------------------------------ small pieces
def chip(label: str, value: str | None = None, kind: str = "") -> str:
    inner = f"{esc(label)} <b>{esc(value)}</b>" if value is not None else esc(label)
    return f'<span class="sc-chip {esc(kind)}"><span class="dot"></span>{inner}</span>'


def badge(text: str, kind: str = "neutral") -> str:
    return f'<span class="sc-badge {esc(kind)}">{esc(text)}</span>'


def page_header(eyebrow: str, title: str, subtitle: str = "", chips: Iterable[str] = ()) -> None:
    chips_html = f'<div class="sc-chips">{"".join(chips)}</div>' if chips else ""
    sub = f'<p class="sc-sub">{esc(subtitle)}</p>' if subtitle else ""
    _html(f'<div class="sc-header"><div><div class="sc-eyebrow">{esc(eyebrow)}</div><h1 class="sc-title">{esc(title)}</h1>{sub}</div>{chips_html}</div>')


@contextmanager
def card(key: str, title: str | None = None, subtitle: str | None = None, hero: bool = False):
    """A surface card. `key` must be unique on the page (it becomes the CSS hook st-key-card_<key>)."""
    with st.container(key=f"card_hero_{key}" if hero else f"card_{key}"):
        if title:
            sub = f'<div class="sc-card-sub">{esc(subtitle)}</div>' if subtitle else ""
            _html(f'<div class="sc-card-title">{esc(title)}</div>{sub}')
        yield


def lead(parts: Iterable[tuple[str, bool]]) -> None:
    """One summary sentence; parts are (text, emphasised). Emphasised parts are the computed figures."""
    _html('<p class="sc-lead">' + "".join(f'<span class="k">{esc(t)}</span>' if k else esc(t) for t, k in parts) + "</p>")


def callout(text: str, kind: str = "info", title: str | None = None) -> None:
    icon = {"info": "i", "warn": "!", "danger": "×", "ok": "✓"}.get(kind, "i")
    head = f"<b>{esc(title)}</b> " if title else ""
    _html(f'<div class="sc-callout {esc(kind)}" role="note"><span class="ic" aria-hidden="true">{icon}</span><div>{head}{esc(text)}</div></div>')


def stat_tiles(tiles: Iterable[tuple[object, str, str]]) -> None:
    """tiles: (value, label, kind) with kind in '', 'high', 'medium', 'low', 'ok'."""
    body = "".join(f'<div class="sc-stat {esc(k)}"><div class="n">{esc(v)}</div><div class="l">{esc(label)}</div></div>' for v, label, k in tiles)
    _html(f'<div class="sc-stats">{body}</div>')


def progress(pct: float) -> str:
    pct = 0.0 if pct is None or (isinstance(pct, float) and math.isnan(pct)) else max(0.0, min(100.0, float(pct)))
    return f'<div class="sc-bar" role="img" aria-label="{pct:.0f} percent"><span style="width:{pct:.1f}%"></span></div>'


def steps(items: Iterable[tuple[str, str]]) -> None:
    _html('<div class="sc-steps">' + "".join(f'<div class="sc-step"><h4>{esc(t)}</h4><p>{esc(d)}</p></div>' for t, d in items) + "</div>")


def bullet_list(items: Iterable[str]) -> None:
    _html('<ul class="sc-list">' + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>")


def key_values(pairs: Iterable[tuple[str, object]]) -> None:
    _html('<dl class="sc-kv">' + "".join(f"<dt>{esc(k)}</dt><dd>{esc(v)}</dd>" for k, v in pairs) + "</dl>")


def code_block(text: str) -> None:
    _html(f'<pre class="sc-code" tabindex="0">{esc(text)}</pre>')


def empty_state(title: str, text: str) -> None:
    _html(f'<div class="sc-empty"><div class="orb" aria-hidden="true"></div><h2>{esc(title)}</h2><p>{esc(text)}</p></div>')


def side_label(text: str) -> None:
    _html(f'<div class="sc-side-label">{esc(text)}</div>')


# ------------------------------------------------------------------------------------------------ tables
_PCT = re.compile(r"(rate|share|retention|ratio|pct|percent|^ci_)", re.I)
_MONEY = re.compile(r"(^|_)(mrr|arr|revenue|amount|value)(_|$)|net_new|churned_mrr|new_mrr", re.I)


def fmt_cell(column: str, v: object) -> str:
    """Human formatting that never changes the underlying value's meaning (rates as %, money as $, NaN as a dash)."""
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NaT:
        return "–"
    if isinstance(v, (bool,)):
        return "Yes" if v else "No"
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "isoformat") and not isinstance(v, str):
        return str(v)[:10]
    if isinstance(v, (int,)) or (hasattr(v, "dtype") and str(getattr(v, "dtype", "")).startswith(("int", "uint"))):
        return f"{int(v):,}"
    if isinstance(v, float):
        if _PCT.search(column) and abs(v) <= 5:
            return f"{v:.1%}"
        if _MONEY.search(column):
            return f"${v:,.0f}"
        return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return str(v)


def html_table(df: pd.DataFrame, max_rows: int = 100) -> None:
    """Escaped, theme-aware table (st.dataframe draws on a canvas that CSS cannot theme)."""
    if df is None or df.empty:
        _html('<div class="sc-table-note">No rows.</div>')
        return
    shown = df.head(max_rows)
    num = [pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c]) for c in df.columns]
    head = "".join(f'<th scope="col"{" class=num" if n else ""}>{esc(c)}</th>' for c, n in zip(df.columns, num))
    rows = []
    for _, r in shown.iterrows():
        cells = "".join(f'<td{" class=num" if n else ""} title="{esc(fmt_cell(c, r[c]))}">{esc(fmt_cell(c, r[c]))}</td>' for c, n in zip(df.columns, num))
        rows.append(f"<tr>{cells}</tr>")
    note = f'<div class="sc-table-note">Showing the first {len(shown):,} of {len(df):,} rows. Download the CSV for all rows.</div>' if len(df) > max_rows else ""
    _html(f'<div class="sc-tablewrap" tabindex="0"><table class="sc-table"><thead><tr>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>{note}')
