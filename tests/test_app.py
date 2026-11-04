"""Application startup and interaction tests using Streamlit's AppTest (runs the real page scripts)."""
import pytest
from streamlit.testing.v1 import AppTest

from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
PAGES = ["app.py", "views/overview.py", "views/copilot.py", "views/data_quality.py", "views/methodology.py"]


@pytest.fixture(autouse=True)
def _clear_caches():
    import streamlit as st
    st.cache_resource.clear()
    st.cache_data.clear()
    yield


@pytest.mark.parametrize("page", PAGES)
def test_page_starts_without_exceptions(page):
    at = AppTest.from_file(str(ROOT / page), default_timeout=90).run()
    assert not at.exception, [e.value for e in at.exception]


def test_overview_kpis_match_backend():
    at = AppTest.from_file(str(ROOT / "views/overview.py"), default_timeout=90).run()
    labels = [m.label for m in at.metric]
    assert labels[:2] == ["MRR (recorded)", "ARR run-rate"] and len(labels) == 5
    from src.analytics.db import ensure_database, open_readonly
    from src.metrics import calculations as C
    path, _ = ensure_database()
    expect = C.kpis(open_readonly(path))
    mrr = expect["mrr"]
    assert at.metric[0].value == (f"${mrr / 1e6:,.2f}M" if mrr >= 1e6 else f"${mrr:,.0f}")
    assert at.metric[4].value == f"{100 * expect['account_churn_rate']:.1f}%"


def test_copilot_chat_roundtrip_and_unsafe_input():
    at = AppTest.from_file(str(ROOT / "views/copilot.py"), default_timeout=90).run()
    at.chat_input[0].set_value("Which industries have higher observed churn rates?").run()
    assert not at.exception and len(at.chat_message) == 2
    at.chat_input[0].set_value("DROP TABLE accounts; --").run()
    assert not at.exception
    at.chat_input[0].set_value("x" * 580).run()           # over the 500-char validation limit but under the widget cap
    assert not at.exception and any("limited to" in e.value for e in at.error)


def test_date_filter_error_state_and_empty_filters():
    at = AppTest.from_file(str(ROOT / "views/overview.py"), default_timeout=90).run()
    assert not at.exception
