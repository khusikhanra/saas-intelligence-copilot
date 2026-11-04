"""Shared Streamlit helpers: database access, cached analyses, filters, and source/status information."""
from __future__ import annotations

import streamlit as st

from src.analytics.db import ensure_database, open_readonly
from src.metrics import calculations as C
from src.metrics.calculations import Filters


@st.cache_resource(show_spinner="Preparing the analytical database...")
def get_db():
    path, source = ensure_database()
    return open_readonly(path), source


def page_setup(title: str = "", icon: str = ""):
    """Open the (cached, read-only) database. Shows a clear error and stops if the data cannot be prepared."""
    try:
        con, source = get_db()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:   # ValueError includes SchemaError
        st.error(str(exc))
        st.stop()
    return con, source


@st.cache_data(show_spinner=False)
def cached_analysis(_con, analysis_id: str, params: tuple, filters: Filters):
    from src.analytics.catalog import run_analysis
    return run_analysis(_con, analysis_id, dict(params), filters)


@st.cache_data(show_spinner=False)
def cached_series(_con, filters: Filters):
    return C.monthly_series(_con, filters)


@st.cache_data(show_spinner=False)
def cached_kpis(_con, filters: Filters):
    return C.kpis(_con, filters)


@st.cache_data(show_spinner=False)
def cached_insights(_con, filters: Filters):
    from src.analytics.insights import build_insights
    return build_insights(_con, filters)


@st.cache_data(show_spinner=False)
def cached_options(_con):
    return C.filter_options(_con), C.data_start(_con), C.snapshot_date(_con)


@st.cache_data(show_spinner=False)
def cached_build_info(_con) -> dict:
    """What the database was built from (recorded at build time) plus its table row counts."""
    cur = _con.cursor()
    try:
        info = dict(cur.execute("SELECT key, value FROM _meta").fetchall())
        info["rows"] = {t: cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                        for t in ("accounts", "subscriptions", "churn_events", "support_tickets", "feature_usage")}
    finally:
        cur.close()
    return info


def source_chips(source: str, info: dict) -> list[str]:
    from src.ui import kit

    chips = [kit.chip("Source", source.capitalize(), "warn" if source == "demo sample" else "ok"), kit.chip("Synthetic data", None, "accent")]
    if info.get("snapshot_date"):
        chips.append(kit.chip("Snapshot", info["snapshot_date"]))
    if info.get("built_at"):
        chips.append(kit.chip("Built", info["built_at"].replace(" UTC", "")))
    return chips


def dataset_banner(source: str):
    """Plain, always-visible disclosure; the demo-sample warning only appears for the demo sample."""
    from src.ui import kit

    if source == "demo sample":
        kit.callout("You are viewing the small bundled demo sample (60 accounts), not the full dataset. Numbers will differ from the full data.", "warn", "Demo sample.")


def filter_bar(con) -> Filters:
    """Filters rendered inline at the top of the page (collapsed by default)."""
    opts, d0, d1 = cached_options(con)
    with st.expander("Filters", expanded=False, icon=":material/tune:"):
        c1, c2 = st.columns([1.3, 2.7])
        rng = c1.date_input("Date range", value=(d0, d1), min_value=d0, max_value=d1,
                            help="Trend charts use this month range. Account-level views filter by signup date.")
        r1 = c2.columns(4)
        ind = r1[0].multiselect("Industry", opts["industry"])
        cty = r1[1].multiselect("Country", opts["country"])
        ref = r1[2].multiselect("Referral source", opts["referral_source"])
        plan = r1[3].multiselect("Plan", opts["plan_tier"], help="Subscription plan for revenue charts; account plan (as recorded) for account-level churn views.")
    start, end = (rng[0], rng[1]) if isinstance(rng, (tuple, list)) and len(rng) == 2 else (d0, d1)
    return Filters(start=start, end=end, industries=tuple(ind), countries=tuple(cty), referral_sources=tuple(ref), plans=tuple(plan))
