"""Application shell: theme state + persistence, CSS injection, logo, and the appearance controls in the sidebar.

Runs once per script run from app.py (the entry point is the frame around every page), so styling is defined in exactly one place.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from src.ui import kit
from src.ui import theme as T

_ASSETS = Path(__file__).parent / "assets"


def _restore(key: str, valid: tuple[str, ...], fallback: str) -> str:
    """Keep the last valid selection if a control ever reports an invalid value (e.g. None)."""
    value = st.session_state.get(key)
    if value not in valid:
        value = st.session_state.get(f"_{key}_last") or fallback
    st.session_state[key] = value
    st.session_state[f"_{key}_last"] = value
    return value


def init_state() -> tuple[str, str]:
    """Session state wins; otherwise the URL (survives a browser refresh); otherwise defaults."""
    theme, accent = T.initial_selection(st.session_state, dict(st.query_params))
    st.session_state.setdefault("ui_theme", theme)
    st.session_state.setdefault("ui_accent", accent)
    return (_restore("ui_theme", T.THEME_NAMES, T.DEFAULT_THEME), _restore("ui_accent", T.ACCENT_NAMES, T.DEFAULT_ACCENT))


def _sync_query_params(theme: str, accent: str) -> None:
    # navigating between pages clears the URL's query string; re-writing it each run keeps a refresh on the chosen theme
    for k, v in (("theme", theme), ("accent", accent)):
        if st.query_params.get(k) != v:
            st.query_params[k] = v


def apply() -> tuple[str, str]:
    theme, accent = init_state()
    st.markdown(f"<style>{T.build_css(theme, accent)}</style>", unsafe_allow_html=True)
    mode = T.THEMES[theme].mode
    logo = _ASSETS / ("logo_light.svg" if mode == "light" else "logo_dark.svg")
    st.logo(str(logo), size="large")
    with st.sidebar:
        kit.side_label("Appearance")
        st.segmented_control("Theme", T.THEME_NAMES, key="ui_theme", required=True, label_visibility="collapsed", width="stretch")
        st.selectbox("Accent colour", T.ACCENT_NAMES, key="ui_accent", help="Auto uses each theme's signature accent.")
        kit.side_label("About")
        st.markdown('<div class="sc-side-note">Read-only analytics on recorded subscription data. Answers come from documented metric functions or validated SQL, never from a model\'s memory.</div>',
                    unsafe_allow_html=True)
    # read-only from here on: Streamlit forbids assigning to a widget's key after the widget has been created in this run
    _sync_query_params(*T.sanitize(st.session_state.get("ui_theme"), st.session_state.get("ui_accent")))
    return theme, accent
