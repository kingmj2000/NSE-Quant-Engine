"""Design tokens + global Qt stylesheet for the desktop app.

ONE place for every colour, gradient, corner radius, spacing step and font
size the native UI uses. Widgets never hard-code a hex value or a radius:
they pick an objectName / dynamic property that this stylesheet styles, or
call `tone_fg()` for rich-text spans.

Why this file exists
--------------------
The previous UI carried ~73 inline `setStyleSheet(...)` calls with their own
colours and radii across five modules, on top of one global sheet that did not
style QComboBox / QLineEdit at all. Every control therefore picked up a
slightly different edge, which is what made a recolour look erratic.

Rules (keep them; tests assert the radius scale):
    * Surfaces  (cards, tables, panels, drawer sections)  -> RADIUS_LG
    * Controls  (buttons, inputs, combos, pills, tabs, nav)  -> RADIUS_MD
    * Hairlines (progress bars, scroll handles, check boxes) -> RADIUS_SM
    * Colour means state: a tinted card border = needs attention
      (amber / red) or confirmed good (green). Informational cards stay neutral.

Pure Python — no Qt import — so tests and the HTML dashboard can read the
tokens without PySide6.
"""
from __future__ import annotations

from pathlib import Path

THEME_NAME = "Midnight Sapphire"

# ---- Radius scale -----------------------------------------------------------
RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14
RADII = (RADIUS_SM, RADIUS_MD, RADIUS_LG)

# ---- Spacing scale (px) ------------------------------------------------------
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 20, "xxl": 24}
CARD_PAD = 16          # inner padding of every card
GAP = 12               # gap between cards
CONTROL_MIN_H = 22     # content height of every button / input / combo

# ---- Type scale (px) ---------------------------------------------------------
FONT_UI = "'Segoe UI Variable Text', 'Segoe UI', 'Inter', sans-serif"
FONT_DISPLAY = "'Segoe UI Variable Display', 'Segoe UI', 'Inter', sans-serif"
FONT_MONO = "'Cascadia Mono', 'Consolas', monospace"
TYPE = {"meta": 11, "small": 12, "body": 13, "title": 15, "page": 22, "metric": 24}

# ---- Colour tokens -------------------------------------------------------------
# "Midnight Sapphire" — a trading-terminal palette:
#   * deep navy canvas drifting to petrol, so long sessions stay easy on the eyes
#   * sapphire -> cyan accent for actions and selection (the colour of "interactive"
#     in most broker terminals), deliberately NOT green or red
#   * green / red stay reserved for up / down and good / bad state
#   * champagne gold only on the brand mark — the one premium touch
C = {
    # canvas
    "bg0": "#050A13",
    "bg1": "#07101D",
    "bg2": "#0B1A30",
    "bg3": "#061820",
    # surfaces (on top of the canvas)
    "surface_top": "rgba(255,255,255,0.058)",
    "surface_bot": "rgba(255,255,255,0.024)",
    "surface2": "rgba(255,255,255,0.045)",
    "surface2_hover": "rgba(255,255,255,0.075)",
    "popup": "#0E1C33",
    # lines
    "line": "rgba(128,168,232,0.14)",
    "line_strong": "rgba(128,168,232,0.28)",
    # text
    "text": "#E6EDF7",
    "text2": "#9FB0C8",
    "text3": "#67799A",
    "white": "#FFFFFF",
    # accent (sapphire -> cyan)
    "accent_a": "#2F6BFF",
    "accent_b": "#1FB8D6",
    "accent_a_hover": "#4B80FF",
    "accent_b_hover": "#3CCAE4",
    "accent_a_pressed": "#2457D6",
    "accent_b_pressed": "#1696B0",
    "accent": "#4C8DFF",
    "accent_soft": "#9CC0FF",
    "accent_bg": "rgba(47,107,255,0.20)",
    "accent_bg_strong": "rgba(47,107,255,0.32)",
    "glow_a": "rgba(47,107,255,0.16)",      # canvas / hero tints
    "glow_b": "rgba(31,184,214,0.10)",
    # brand (champagne gold)
    "brand_a": "#F1D79A",
    "brand_b": "#C2913F",
    "brand_text": "#1A1405",
}

# Semantic tones. Keys match the `tone` / `accent` names already used across
# the app (teal, amber, red, blue, violet, green, indigo, dim).
TONES = {
    "green":  {"fg": "#34D399", "bg": "rgba(52,211,153,0.14)",  "line": "rgba(52,211,153,0.38)"},
    "teal":   {"fg": "#2DD4BF", "bg": "rgba(45,212,191,0.13)",  "line": "rgba(45,212,191,0.34)"},
    "amber":  {"fg": "#F5B547", "bg": "rgba(245,181,71,0.14)",  "line": "rgba(245,181,71,0.40)"},
    "red":    {"fg": "#F87171", "bg": "rgba(248,113,113,0.15)", "line": "rgba(248,113,113,0.42)"},
    "blue":   {"fg": "#60A5FA", "bg": "rgba(96,165,250,0.13)",  "line": "rgba(96,165,250,0.32)"},
    "violet": {"fg": "#A78BFA", "bg": "rgba(167,139,250,0.14)", "line": "rgba(167,139,250,0.34)"},
    "indigo": {"fg": "#818CF8", "bg": "rgba(129,140,248,0.13)", "line": "rgba(129,140,248,0.32)"},
    "dim":    {"fg": "#9FB0C8", "bg": "rgba(255,255,255,0.06)", "line": "rgba(128,168,232,0.14)"},
}
# Tones that tint a card border. Everything else renders as a neutral card.
STATE_TONES = ("green", "amber", "red")

# Leading glyphs that already say what a line is. They become the coloured
# marker instead of sitting after a generic dot ("● ⚠ New risk flag" -> "▲ New
# risk flag"). ⚠ and ⏰ render as colour emoji on Windows, so they map to
# plain glyphs that take the tone colour.
_MARKERS = {"↑": "↑", "↓": "↓", "↕": "↕", "⇄": "⇄", "✓": "✓", "●": "●",
            "⚠": "▲", "⏰": "◷", "▲": "▲", "▼": "▼"}


def split_marker(value: str) -> tuple[str, str]:
    """('▲', 'New risk flag: X') for '⚠ New risk flag: X'; ('●', value) otherwise."""
    v = (value or "").lstrip()
    for k, m in _MARKERS.items():
        if v.startswith(k):
            return m, v[len(k):].lstrip("\ufe0f ").lstrip()
    return "●", value


ASSETS = Path(__file__).resolve().parent / "assets"


def tone_fg(tone: str | None) -> str:
    """Foreground hex for a tone; unknown tones fall back to body text."""
    return TONES.get(str(tone or ""), {}).get("fg", C["text"])


def tone_bg(tone: str | None) -> str:
    return TONES.get(str(tone or ""), TONES["dim"])["bg"]


def _asset(name: str) -> str:
    # Qt stylesheet url() needs forward slashes; quoting handles spaces such as
    # "OneDrive - DESRI Project Management" in the run-folder path.
    return '"' + (ASSETS / name).as_posix() + '"'


def _accent_gradient(direction: str = "diag") -> str:
    x2, y2 = (1, 1) if direction == "diag" else (1, 0)
    return (f"qlineargradient(x1:0, y1:0, x2:{x2}, y2:{y2}, "
            f"stop:0 {C['accent_a']}, stop:1 {C['accent_b']})")


def _surface_gradient() -> str:
    return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {C['surface_top']}, stop:1 {C['surface_bot']})")


def build_qss() -> str:
    """The complete application stylesheet."""
    sm, md, lg = RADIUS_SM, RADIUS_MD, RADIUS_LG
    t = TYPE
    tone_rules = []
    for name, tv in TONES.items():
        tone_rules.append(
            f'QLabel#Pill[tone="{name}"] {{ background: {tv["bg"]}; color: {tv["fg"]}; }}')
        tone_rules.append(f'QLabel#Metric[tone="{name}"] {{ color: {tv["fg"]}; }}')
        tone_rules.append(f'QLabel#Value[tone="{name}"] {{ color: {tv["fg"]}; }}')
        tone_rules.append(
            f'QLabel#Banner[tone="{name}"] {{ background: {tv["bg"]}; color: {tv["fg"]}; '
            f'border: 1px solid {tv["line"]}; }}')
    for name in STATE_TONES:
        tone_rules.append(
            f'QFrame#Card[accent="{name}"] {{ border: 1px solid {TONES[name]["line"]}; }}')
    tones = "\n".join(tone_rules)

    return f"""
/* ===== {THEME_NAME} — generated by ui/theme.py; edit tokens there, not here ===== */
QWidget {{ font-family: {FONT_UI}; font-size: {t['body']}px; color: {C['text']}; background: transparent; }}
QMainWindow {{ background: {C['bg1']}; }}
QWidget#AppRoot {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {C['bg1']}, stop:0.55 {C['bg2']}, stop:1 {C['bg3']});
}}
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget,
QAbstractScrollArea {{ background: transparent; border: none; }}
QLabel {{ background: transparent; }}
QToolTip {{
    background: {C['popup']}; color: {C['text']}; border: 1px solid {C['line_strong']};
    padding: 6px 8px; font-size: {t['small']}px;
}}

/* ---------- Shell ---------- */
QFrame#Sidebar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {C['glow_a']}, stop:0.45 rgba(255,255,255,0.02), stop:1 {C['glow_b']});
    border-right: 1px solid {C['line']};
}}
QLabel#BrandMark {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {C['brand_a']}, stop:1 {C['brand_b']});
    color: {C['brand_text']}; border-radius: {md}px;
    font-family: {FONT_DISPLAY}; font-size: 15px; font-weight: 800;
}}
QLabel#BrandName {{ font-family: {FONT_DISPLAY}; font-size: {t['title']}px; font-weight: 700; color: {C['white']}; }}
QLabel#BrandSub {{ font-size: {t['meta']}px; color: {C['text3']}; }}
QPushButton#NavItem {{
    background: transparent; color: {C['text2']}; border: 1px solid transparent;
    border-radius: {md}px; padding: 9px 12px; text-align: left;
    font-size: {t['body']}px; font-weight: 600;
}}
QPushButton#NavItem:hover {{ background: {C['surface2']}; color: {C['text']}; }}
QPushButton#NavItem:checked {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {C['accent_bg_strong']}, stop:1 {C['glow_b']});
    border: 1px solid {C['line_strong']}; color: {C['white']};
}}
QPushButton#NavItem:focus {{ border: 1px solid {C['accent']}; }}
QFrame#SubNav {{ background: transparent; border: none; margin-left: 20px; border-left: 1px solid {C['line']}; }}
QPushButton#NavSubItem {{
    background: transparent; color: {C['text2']}; border: 1px solid transparent;
    border-radius: {md}px; padding: 6px 10px 6px 14px; margin-left: 8px; text-align: left;
    font-size: {t['small']}px; font-weight: 600; min-height: 18px;
}}
QPushButton#NavSubItem:hover {{ background: {C['surface2']}; color: {C['text']}; }}
QPushButton#NavSubItem:checked {{ background: {C['surface2_hover']}; border-color: {C['line']}; color: {C['accent_soft']}; }}
QPushButton#NavSubItem:focus {{ border-color: {C['accent']}; }}
QFrame#SideStatus {{
    background: {_surface_gradient()}; border: 1px solid {C['line']}; border-radius: {lg}px;
}}
QFrame#TopBar {{ background: transparent; border: none; border-bottom: 1px solid {C['line']}; }}
QLabel#PageTitle {{ font-family: {FONT_DISPLAY}; font-size: {t['page']}px; font-weight: 700; color: {C['white']}; }}
QLabel#PageSub {{ font-size: {t['small']}px; color: {C['text2']}; }}
QFrame#Drawer {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {C['bg2']}, stop:1 {C['bg1']});
    border-left: 1px solid {C['line']};
}}
QStatusBar {{ background: transparent; color: {C['text3']}; border-top: 1px solid {C['line']}; }}
QStatusBar::item {{ border: none; }}

/* ---------- Surfaces ---------- */
QFrame#Card {{
    background: {_surface_gradient()};
    border: 1px solid {C['line']};
    border-radius: {lg}px;
}}
QFrame#Card[variant="hero"] {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {C['accent_bg']}, stop:0.6 rgba(255,255,255,0.03), stop:1 {C['glow_b']});
}}
QFrame#Tile {{ background: {C['surface2']}; border: 1px solid {C['line']}; border-radius: {lg}px; }}
QFrame#Divider {{ background: {C['line']}; border: none; max-height: 1px; min-height: 1px; }}
{tones}

/* ---------- Text roles ---------- */
QLabel#Header, QLabel#CardTitle {{ font-family: {FONT_DISPLAY}; font-size: {t['title']}px; font-weight: 700; color: {C['white']}; }}
QLabel#Section {{ font-size: {t['small']}px; font-weight: 700; color: {C['text2']}; }}
QLabel#Sub, QLabel#Meta {{ font-size: {t['small']}px; color: {C['text2']}; }}
QLabel#Faint {{ font-size: {t['meta']}px; color: {C['text3']}; }}
QLabel#Empty {{ font-size: {t['small']}px; color: {C['text3']}; font-style: italic; }}
QLabel#Body {{ font-size: {t['body']}px; color: {C['text']}; }}
QLabel#KpiTitle {{ font-size: {t['small']}px; font-weight: 600; color: {C['text2']}; }}
QLabel#Metric {{ font-family: {FONT_DISPLAY}; font-size: {t['metric']}px; font-weight: 700; color: {C['white']}; }}
QLabel#MetricSmall {{ font-family: {FONT_DISPLAY}; font-size: 18px; font-weight: 700; color: {C['white']}; }}
QLabel#Value {{ font-size: {t['body']}px; font-weight: 700; color: {C['text']}; }}
QLabel#Symbol {{ font-family: {FONT_DISPLAY}; font-size: 17px; font-weight: 800; color: {C['white']}; }}
QLabel#Hero {{ font-family: {FONT_DISPLAY}; font-size: 26px; font-weight: 800; color: {C['white']}; }}
QLabel#Banner {{ border-radius: {md}px; padding: 9px 12px; font-weight: 600; font-size: {t['small']}px; }}
QLabel#Pill {{
    padding: 3px 10px; border-radius: {md}px; font-weight: 600; font-size: {t['meta']}px;
    background: {TONES['dim']['bg']}; color: {TONES['dim']['fg']};
}}
QLabel#PillAge {{
    padding: 3px 10px; border-radius: {md}px; font-weight: 600; font-size: {t['meta']}px;
    background: {C['surface2']}; color: {C['text2']}; border: 1px solid {C['line']};
}}

/* ---------- Buttons ---------- */
QPushButton, QToolButton#Drawer {{
    background: {C['surface2']}; color: {C['text']};
    border: 1px solid {C['line']}; border-radius: {md}px;
    padding: 5px 14px; min-height: {CONTROL_MIN_H}px;
    font-size: {t['body']}px; font-weight: 600;
}}
QPushButton:hover, QToolButton#Drawer:hover {{ background: {C['surface2_hover']}; border-color: {C['line_strong']}; }}
QPushButton:pressed, QToolButton#Drawer:pressed {{ background: {C['accent_bg']}; }}
QPushButton:focus, QToolButton#Drawer:focus {{ border-color: {C['accent']}; }}
QPushButton:disabled, QToolButton#Drawer:disabled {{ color: {C['text3']}; background: rgba(255,255,255,0.02); }}
QToolButton#Drawer:checked {{ background: {C['accent_bg']}; border-color: {C['line_strong']}; color: {C['white']}; }}
QPushButton#Primary {{
    background: {_accent_gradient()}; color: {C['white']}; border: 1px solid rgba(255,255,255,0.14);
    font-weight: 700; padding: 5px 18px;
}}
QPushButton#Primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {C['accent_a_hover']}, stop:1 {C['accent_b_hover']});
}}
QPushButton#Primary:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {C['accent_a_pressed']}, stop:1 {C['accent_b_pressed']});
}}
QPushButton#Primary:disabled {{ background: rgba(255,255,255,0.05); color: {C['text3']}; border-color: {C['line']}; }}
QPushButton#Ghost {{ background: transparent; }}
QPushButton#Ghost:hover {{ background: {C['surface2']}; }}

/* ---------- Inputs ---------- */
QLineEdit, QComboBox {{
    background: {C['surface2']}; color: {C['text']};
    border: 1px solid {C['line']}; border-radius: {md}px;
    padding: 5px 12px; min-height: {CONTROL_MIN_H}px;
    selection-background-color: {C['accent_bg_strong']};
}}
QLineEdit:hover, QComboBox:hover {{ border-color: {C['line_strong']}; }}
QLineEdit:focus, QComboBox:focus, QComboBox:on {{ border-color: {C['accent']}; }}
QComboBox {{ padding-right: 30px; combobox-popup: 0; }}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 28px; border: none; background: transparent;
}}
QComboBox::down-arrow {{ image: url({_asset('chevron-down.png')}); width: 12px; height: 12px; }}
QComboBox::down-arrow:hover, QComboBox::down-arrow:on {{ image: url({_asset('chevron-down-hover.png')}); }}
QComboBox QAbstractItemView {{
    background: {C['popup']}; color: {C['text']};
    border: 1px solid {C['line_strong']}; border-radius: {md}px;
    padding: 4px; outline: 0;
    selection-background-color: {C['accent_bg_strong']}; selection-color: {C['white']};
}}
QComboBox QAbstractItemView::item {{ min-height: 28px; padding: 2px 10px; border-radius: {sm}px; }}
QComboBox QAbstractItemView::item:hover {{ background: {C['surface2_hover']}; }}
QComboBox QAbstractItemView::item:selected {{ background: {C['accent_bg_strong']}; color: {C['white']}; }}
QCheckBox {{ spacing: 8px; color: {C['text2']}; font-weight: 600; }}
QCheckBox:hover {{ color: {C['text']}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: {sm}px;
    border: 1px solid {C['line_strong']}; background: {C['surface2']};
}}
QCheckBox::indicator:hover {{ border-color: {C['accent']}; }}
QCheckBox::indicator:checked {{
    background: {_accent_gradient()}; border-color: {C['accent']};
    image: url({_asset('check.png')});
}}
QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {C['surface2']}; color: {C['text']};
    border: 1px solid {C['line']}; border-radius: {md}px; padding: 8px;
    selection-background-color: {C['accent_bg_strong']};
}}
QPlainTextEdit#Log {{ font-family: {FONT_MONO}; font-size: {t['meta']}px; color: {C['text2']}; }}

/* ---------- Tabs (nested section switchers) ---------- */
QTabWidget::pane {{ border: none; background: transparent; top: 6px; }}
QTabWidget::tab-bar {{ left: 0px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: transparent; color: {C['text2']};
    border: 1px solid transparent; border-radius: {md}px;
    padding: 6px 14px; margin-right: 6px; font-weight: 600;
}}
QTabBar::tab:hover:!selected {{ background: {C['surface2']}; color: {C['text']}; }}
QTabBar::tab:selected {{ background: {C['accent_bg']}; border-color: {C['line_strong']}; color: {C['white']}; }}

/* ---------- Tables ---------- */
QTableView, QTableWidget, QListView, QListWidget {{
    background: transparent;
    alternate-background-color: rgba(255,255,255,0.028);
    border: 1px solid {C['line']}; border-radius: {lg}px;
    gridline-color: transparent; outline: 0; show-decoration-selected: 1;
    selection-background-color: {C['accent_bg_strong']}; selection-color: {C['white']};
}}
QTableView[embedded="true"], QTableWidget[embedded="true"] {{ background: transparent; border: none; border-radius: 0px; }}
QTableView::item, QTableWidget::item {{ padding: 4px 10px; border: none; }}
QTableView::item:hover, QTableWidget::item:hover {{ background: rgba(255,255,255,0.04); }}
QTableView::item:selected, QTableWidget::item:selected,
QTableView::item:selected:active, QTableView::item:selected:!active {{
    background: {C['accent_bg_strong']}; color: {C['white']}; border: 0px solid transparent;
}}
QTableView::item:focus, QTableWidget::item:focus {{ border: 0px solid transparent; outline: none; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: transparent; color: {C['text3']};
    padding: 8px 10px; border: none; border-bottom: 1px solid {C['line']};
    font-size: {t['meta']}px; font-weight: 700;
}}
QHeaderView::section:hover {{ color: {C['text']}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}

/* ---------- Misc ---------- */
QProgressBar {{
    background: rgba(255,255,255,0.06); border: none; border-radius: {sm}px;
    min-height: 8px; max-height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {_accent_gradient('horizontal')}; border-radius: {sm}px; }}
QProgressBar[tone="green"]::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {TONES['teal']['fg']}, stop:1 {TONES['green']['fg']});
}}
QFrame#BarTrack {{ background: rgba(255,255,255,0.06); border: none; border-radius: {sm}px; }}
QFrame#BarFill {{ background: {_accent_gradient('horizontal')}; border: none; border-radius: {sm}px; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.12); border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {C['accent']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: rgba(255,255,255,0.12); border-radius: 3px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: {C['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QMessageBox {{ background: {C['bg2']}; }}
QMessageBox QLabel {{ color: {C['text']}; }}
"""


# Window palette — keeps native-drawn areas (popups, message boxes) on theme.
PALETTE = {
    "Window": C["bg1"],
    "Base": C["bg1"],
    "AlternateBase": C["bg2"],
    "Text": C["text"],
    "WindowText": C["text"],
    "ButtonText": C["text"],
    "Button": C["bg2"],
    "Highlight": C["accent_a"],
    "HighlightedText": "#FFFFFF",
    "ToolTipBase": C["popup"],
    "ToolTipText": C["text"],
    "PlaceholderText": C["text3"],
}


# ---- HTML dashboard ------------------------------------------------------------
# dashboard_html_builder.py renders these into its <style> and <script>, so the
# browser dashboard can never drift from the desktop app.
def _rgba(hex_: str, alpha: float) -> str:
    h = hex_.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


def css_vars() -> str:
    t = TONES
    lines = {
        "--bg": C["bg1"], "--bg2": C["bg2"], "--bg3": C["bg3"],
        "--panel": f"linear-gradient(180deg,{C['surface_top']},{C['surface_bot']})",
        "--panel2": C["surface2"], "--panel2-hover": C["surface2_hover"], "--popup": C["popup"],
        "--line": C["line"], "--line2": C["line_strong"],
        "--txt": C["text"], "--muted": C["text2"], "--dim": C["text3"],
        "--accent": C["accent"], "--accent-soft": C["accent_soft"],
        "--accent-bg": C["accent_bg"], "--accent-bg-strong": C["accent_bg_strong"],
        "--accent-deep": C["accent_a_pressed"],
        "--accent2": C["accent_b"], "--accent2-soft": C["accent_soft"],
        "--grad": f"linear-gradient(135deg,{C['accent_a']},{C['accent_b']})",
        "--grad-hover": f"linear-gradient(135deg,{C['accent_a_hover']},{C['accent_b_hover']})",
        "--brand": f"linear-gradient(135deg,{C['brand_a']},{C['brand_b']})",
        "--brand-text": C["brand_text"],
        "--glow-a": C["glow_a"], "--glow-b": C["glow_b"],
        "--teal": t["teal"]["fg"], "--teal-bg": t["teal"]["bg"],
        "--amber": t["amber"]["fg"], "--amber-bg": t["amber"]["bg"], "--amber-line": t["amber"]["line"],
        "--green": t["green"]["fg"], "--green-bg": t["green"]["bg"], "--green-line": t["green"]["line"],
        "--red": t["red"]["fg"], "--red-soft": t["red"]["fg"], "--red-bg": t["red"]["bg"], "--red-line": t["red"]["line"],
        "--blue": t["blue"]["fg"], "--blue-soft": t["blue"]["fg"], "--blue-bg": t["blue"]["bg"],
        "--violet": t["violet"]["fg"], "--violet-soft": t["violet"]["fg"], "--violet-bg": t["violet"]["bg"],
        "--r-lg": f"{RADIUS_LG}px", "--r-md": f"{RADIUS_MD}px", "--r-sm": f"{RADIUS_SM}px",
        "--font-ui": FONT_UI.replace("'", '"'), "--font-display": FONT_DISPLAY.replace("'", '"'),
    }
    for g in ("primary", "teal", "amber", "green", "blue", "violet", "neutral"):
        lines[f"--glow-{g}"] = "none"
    lines["--glow"] = "none"
    return ":root{\n" + "\n".join(f"  {k}:{v};" for k, v in lines.items()) + "\n}"


def chart_palette() -> dict:
    t = TONES
    return {
        "text": C["text"], "text2": C["text2"], "grid": C["line"],
        "teal": t["teal"]["fg"], "blue": t["blue"]["fg"], "violet": t["violet"]["fg"],
        "green": t["green"]["fg"], "amber": t["amber"]["fg"], "red": t["red"]["fg"],
        "accent": C["accent"],
        "amberWash": _rgba(t["amber"]["fg"], 0.06), "violetWash": _rgba(t["violet"]["fg"], 0.05),
        "amberLine": _rgba(t["amber"]["fg"], 0.55), "violetLine": _rgba(t["violet"]["fg"], 0.45),
        "tooltip": _rgba(C["popup"], 0.96),
    }
