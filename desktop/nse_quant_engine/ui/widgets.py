"""Shared native widget factories for the desktop app.

Every card, pill, KPI tile, section label and banner in the app is built here,
so they all share one look. Styling comes from ui/theme.py through object
names and dynamic properties — nothing in this module hard-codes a colour or a
corner radius (rich-text dots use theme.tone_fg).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QListView, QTableView, QVBoxLayout,
    QWidget,
)

from ui import theme


def repolish(w: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    try:
        w.style().unpolish(w)
        w.style().polish(w)
    except Exception:
        pass


def pill(text: str, tone: str = "dim") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Pill")
    lbl.setProperty("tone", tone if tone in theme.TONES else "dim")
    repolish(lbl)
    return lbl


def text(value: str, role: str = "Body", tone: str | None = None, wrap: bool = False) -> QLabel:
    """A label in one of the theme text roles (Body, Meta, Faint, Section,
    CardTitle, Value, Metric, MetricSmall, Symbol, Hero, Empty, KpiTitle)."""
    lbl = QLabel(value)
    lbl.setObjectName(role)
    if tone:
        lbl.setProperty("tone", tone)
    lbl.setWordWrap(wrap)
    repolish(lbl)
    return lbl


def section_label(value: str) -> QLabel:
    # Sentence case, semibold, muted — no tracked-out capitals.
    return text(value, "Section")


def empty_note(value: str) -> QLabel:
    return text(value, "Empty", wrap=True)


def banner(value: str, tone: str = "amber") -> QLabel:
    lbl = QLabel(value)
    lbl.setObjectName("Banner")
    lbl.setProperty("tone", tone)
    lbl.setWordWrap(True)
    repolish(lbl)
    return lbl


def card(accent: str | None = None, *, variant: str | None = None,
         spacing: int = 8, margins: int | None = None) -> tuple[QFrame, QVBoxLayout]:
    """A themed surface. `accent` only tints the border for state tones
    (green / amber / red); every other accent renders as a neutral card."""
    frame = QFrame()
    frame.setObjectName("Card")
    if accent:
        frame.setProperty("accent", accent)
    if variant:
        frame.setProperty("variant", variant)
    lay = QVBoxLayout(frame)
    m = theme.CARD_PAD if margins is None else margins
    lay.setContentsMargins(m, m - 2, m, m)
    lay.setSpacing(spacing)
    repolish(frame)
    return frame, lay


def tile() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Tile")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(2)
    return frame, lay


def kpi_card(title: str, value: str, tone: str = "dim", subtitle: str = "",
             *, compact: bool = False) -> QFrame:
    """Metric tile: muted title, tone-coloured value, optional faint subtitle."""
    frame, lay = card(tone, spacing=3, margins=14)
    lay.addWidget(text(title, "KpiTitle", wrap=True))
    val = text(value, "MetricSmall" if (compact or len(str(value)) > 14) else "Metric",
               tone=tone if tone in theme.TONES else None, wrap=True)
    lay.addWidget(val)
    if subtitle:
        lay.addWidget(text(subtitle, "Faint", wrap=True))
    lay.addStretch()
    return frame


def stat(title: str, value: str, tone: str | None = None) -> QWidget:
    """Label-over-value pair without a surface (used inside cards)."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(1)
    v.addWidget(text(title, "Faint"))
    v.addWidget(text(value, "Value", tone=tone))
    return w


def dot_line(value: str, tone: str = "dim", *, plain: bool = True) -> QWidget:
    """One status line: tone-coloured marker in a fixed gutter + wrapping text.
    `value` is shown as plain text unless plain=False (trusted rich text)."""
    marker, value = theme.split_marker(value) if plain else ("●", value)
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 1, 0, 1)
    h.setSpacing(8)
    m = QLabel(marker)
    m.setObjectName("Value")
    m.setProperty("tone", tone if tone in theme.TONES else "dim")
    m.setFixedWidth(14)
    m.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
    repolish(m)
    t = QLabel(value)
    t.setObjectName("Body")
    t.setTextFormat(Qt.PlainText if plain else Qt.RichText)
    t.setWordWrap(True)
    h.addWidget(m, 0, Qt.AlignTop)
    h.addWidget(t, 1)
    return w


def divider() -> QFrame:
    f = QFrame()
    f.setObjectName("Divider")
    f.setFixedHeight(1)
    return f


def row(*widgets, spacing: int = 8, stretch_end: bool = True) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(spacing)
    for x in widgets:
        h.addWidget(x)
    if stretch_end:
        h.addStretch()
    return w


def labeled(caption: str, widget: QWidget) -> QWidget:
    """Faint caption stacked above a control — keeps "All"/"Any" combos legible."""
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(4)
    v.addWidget(text(caption, "Faint"))
    v.addWidget(widget)
    return w


def polish_combo(combo: QComboBox) -> QComboBox:
    """Give a combo the themed popup.

    A QListView view makes the ::item rules in the stylesheet apply (the default
    delegate ignores them), and a frameless, translucent popup window stops the
    square window corners from showing behind the rounded list.
    """
    try:
        combo.setView(QListView())
        combo.view().setMouseTracking(True)
        popup = combo.view().window()
        popup.setWindowFlags(popup.windowFlags() | Qt.FramelessWindowHint
                             | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground, True)
    except Exception:
        pass  # cosmetic only — the combo still works with the default popup
    combo.setCursor(Qt.PointingHandCursor)
    return combo


def style_table(table: QTableView, *, embedded: bool = False, row_height: int = 32) -> QTableView:
    """Uniform table chrome: row height, no vertical header, embedded = no frame."""
    try:
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(row_height)
        table.horizontalHeader().setHighlightSections(False)
        table.setShowGrid(False)
        table.setWordWrap(False)
        if embedded:
            table.setProperty("embedded", True)
            repolish(table)
    except Exception:
        pass
    return table


def fit_columns(table: QTableView, *, max_w: int = 280, min_w: int = 56, sample_rows: int = 80) -> None:
    """Size columns to their header + content (sampled), capped, last column stretches.

    Replaces equal-width columns that clipped headers ("aw Score Bucke") and
    elided short values while wide columns sat half empty.
    """
    try:
        h = table.horizontalHeader()
        try:
            h.setResizeContentsPrecision(sample_rows)
        except Exception:
            pass
        table.resizeColumnsToContents()
        model = table.model()
        cols = model.columnCount() if model is not None else 0
        for c in range(cols):
            w = table.columnWidth(c)
            table.setColumnWidth(c, max(min_w, min(w + 14, max_w)))
        h.setStretchLastSection(True)
    except Exception:
        pass


def button_text(label: str) -> str:
    """Qt treats '&' in button text as a shortcut marker ("News & Events" showed
    as "News_Events"); double it so it renders literally."""
    return (label or "").replace("&", "&&")
