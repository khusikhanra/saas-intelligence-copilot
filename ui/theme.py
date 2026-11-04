"""Design tokens, theme state and CSS generation for the whole UI.

Three themes (Dark, Light, Midnight) x five accents. Every colour pair that carries text is checked against WCAG 2.1 AA in
tests/test_ui_theme.py (including on the tinted backgrounds the colours are actually drawn on), so accessibility is a tested
property rather than a hope.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_THEME = "Dark"
DEFAULT_ACCENT = "Auto"
THEME_NAMES = ("Dark", "Light", "Midnight")
ACCENT_NAMES = ("Auto", "Indigo", "Teal", "Violet", "Amber")
_ASSETS = Path(__file__).parent / "assets"


@dataclass(frozen=True)
class Tokens:
    name: str
    mode: str            # "dark" | "light": decides which accent variant and which native look to override
    bg: str              # app background
    sidebar: str
    surface: str         # cards
    surface2: str        # raised / hover / inputs
    border: str
    border_strong: str
    text: str
    muted: str           # secondary text (AA on every surface)
    success: str
    warning: str
    danger: str
    info: str
    shadow: str          # ambient card shadow
    glow: str            # soft accent glow colour (r,g,b)
    default_accent: str


THEMES: dict[str, Tokens] = {
    "Dark": Tokens("Dark", "dark", bg="#0B0D12", sidebar="#0F1218", surface="#151922", surface2="#1D2230", border="#262C3B",
                   border_strong="#374055", text="#E9ECF3", muted="#A3ACBF", success="#34D399", warning="#FBBF24", danger="#F87171",
                   info="#60A5FA", shadow="0 1px 2px rgba(0,0,0,.45), 0 12px 32px -12px rgba(0,0,0,.6)", glow="129,140,248",
                   default_accent="Indigo"),
    "Light": Tokens("Light", "light", bg="#F5F6FA", sidebar="#FFFFFF", surface="#FFFFFF", surface2="#EEF1F7", border="#E1E5EE",
                    border_strong="#C5CCDB", text="#0F172A", muted="#4B5569", success="#065F46", warning="#854D0E", danger="#991B1B",
                    info="#1D4ED8", shadow="0 1px 2px rgba(15,23,42,.06), 0 10px 28px -12px rgba(15,23,42,.18)", glow="79,70,229",
                    default_accent="Indigo"),
    "Midnight": Tokens("Midnight", "dark", bg="#060A18", sidebar="#080E22", surface="#0D1530", surface2="#152044", border="#1E2B55",
                       border_strong="#2E3F78", text="#E6ECFF", muted="#A2B1DC", success="#4ADE80", warning="#FCD34D", danger="#FB7185",
                       info="#7DD3FC", shadow="0 1px 2px rgba(0,0,10,.5), 0 14px 36px -12px rgba(0,5,30,.75)", glow="45,212,191",
                       default_accent="Teal"),
}

# accent colours per mode (the "light" variants are darker so they stay readable as text/links on white and on their own tint)
ACCENTS: dict[str, dict[str, str]] = {
    "Indigo": {"dark": "#8E9BFF", "light": "#4338CA"},
    "Teal": {"dark": "#2DD4BF", "light": "#0B6059"},
    "Violet": {"dark": "#B79CFF", "light": "#6D28D9"},
    "Amber": {"dark": "#FBBF24", "light": "#92400E"},
}


# ------------------------------------------------------------------------------------------------ colour maths
def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def luminance(hex_color: str) -> float:
    def ch(v: int) -> float:
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hex_color)
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.1 contrast ratio between two #RRGGBB colours."""
    a, b = luminance(fg), luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def on_color(bg: str) -> str:
    """Readable text colour (near-black or white) for text placed on `bg`."""
    return "#0B0D12" if contrast_ratio("#0B0D12", bg) >= contrast_ratio("#FFFFFF", bg) else "#FFFFFF"


def rgba(hex_color: str, alpha: float) -> str:
    r, g, b = _rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha})"


def resolve(theme: str | None, accent: str | None) -> tuple[Tokens, str, str]:
    """Return (tokens, accent_hex, accent_name). Unknown values fall back to defaults rather than raising."""
    t = THEMES.get(theme or "", THEMES[DEFAULT_THEME])
    name = accent if accent in ACCENTS else t.default_accent
    return t, ACCENTS[name][t.mode], name


# ------------------------------------------------------------------------------------------------ state
def sanitize(theme: str | None, accent: str | None) -> tuple[str, str]:
    return (theme if theme in THEMES else DEFAULT_THEME), (accent if accent in ACCENT_NAMES else DEFAULT_ACCENT)


def initial_selection(session: dict, query: dict) -> tuple[str, str]:
    """Session state wins (same browser session); otherwise the URL query string (survives a browser refresh); otherwise defaults."""
    theme = session.get("ui_theme") or query.get("theme")
    accent = session.get("ui_accent") or query.get("accent")
    return sanitize(theme, accent)


def current() -> tuple[str, str]:
    """The (theme, accent) selected in this browser session; defaults when nothing is selected (e.g. a page run standalone)."""
    import streamlit as st

    return sanitize(st.session_state.get("ui_theme"), st.session_state.get("ui_accent"))


def css_variables(theme: str, accent: str) -> str:
    t, acc, _ = resolve(theme, accent if accent != DEFAULT_ACCENT else None)
    v = {
        "bg": t.bg, "sidebar": t.sidebar, "surface": t.surface, "surface2": t.surface2, "border": t.border, "border-strong": t.border_strong,
        "text": t.text, "muted": t.muted, "success": t.success, "warning": t.warning, "danger": t.danger, "info": t.info,
        "accent": acc, "on-accent": on_color(acc), "accent-soft": rgba(acc, 0.14), "accent-line": rgba(acc, 0.45),
        "success-soft": rgba(t.success, 0.14), "warning-soft": rgba(t.warning, 0.14), "danger-soft": rgba(t.danger, 0.14),
        "info-soft": rgba(t.info, 0.14), "shadow": t.shadow, "glow": f"rgba({t.glow},.16)",
        "scheme": t.mode,
    }
    return ":root{" + ";".join(f"--sc-{k}:{val}" for k, val in v.items()) + ";color-scheme:" + t.mode + "}"


@lru_cache(maxsize=1)
def _base_css() -> str:
    return (_ASSETS / "app.css").read_text(encoding="utf-8")


def build_css(theme: str, accent: str) -> str:
    """Complete stylesheet for a theme/accent pair: token variables first, then the shared component CSS."""
    return css_variables(*sanitize(theme, accent)) + "\n" + _base_css()


# ------------------------------------------------------------------------------------------------ charts
def chart_palette(theme: str, accent: str) -> list[str]:
    t, acc, _ = resolve(*sanitize(theme, accent))
    base = ["#38BDF8", "#34D399", "#FBBF24", "#F472B6", "#A78BFA", "#FB923C"] if t.mode == "dark" else \
           ["#0369A1", "#047857", "#B45309", "#BE185D", "#6D28D9", "#C2410C"]
    return [acc] + [c for c in base if c.lower() != acc.lower()]


def plotly_layout(theme: str, accent: str) -> dict:
    t, acc, _ = resolve(*sanitize(theme, accent))
    font = 'Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
    return {
        "paper_bgcolor": "rgba(0,0,0,0)", "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": font, "color": t.muted, "size": 12},
        "title": {"font": {"size": 14, "color": t.text}},
        "colorway": chart_palette(theme, accent),
        "xaxis": {"gridcolor": t.border, "linecolor": t.border_strong, "zerolinecolor": t.border_strong, "tickfont": {"color": t.muted},
                  "title": {"font": {"color": t.muted}}},
        "yaxis": {"gridcolor": t.border, "linecolor": t.border, "zerolinecolor": t.border_strong, "tickfont": {"color": t.muted},
                  "title": {"font": {"color": t.muted}}},
        "legend": {"font": {"color": t.muted}, "bgcolor": "rgba(0,0,0,0)"},
        "hoverlabel": {"bgcolor": t.surface2, "bordercolor": t.border_strong, "font": {"color": t.text, "family": font}},
        "margin": {"l": 8, "r": 8, "t": 44, "b": 8},
    }
