"""Render pipeline-generated markdown reports as themed Qt widgets.

Used by the Validation and Trade Plan tabs so section headings, tables, bullet
lists and bold spans no longer look like raw text pasted into a monospaced box.

Public API:
    render_markdown(md_text: str, parent_layout: QVBoxLayout) -> None
        Appends a stack of glass panels (one per `##` section) into the given
        layout. Empty markers (`_No spread summary yet..._`) become italic
        muted note cards. Markdown tables become styled QTableViews.
"""
from __future__ import annotations
import re
from typing import List, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QTableView, QTextBrowser,
    QWidget, QHeaderView,
)


# ------------ tone routing ---------------------------------------------------
_VERDICT_TONE = {
    "insufficient evidence": "amber",
    "insufficient history":  "amber",
    "validation positive":   "green",
    "validation negative":   "red",
    "do not switch":         "red",
    "shadow leads":          "green",
    "review":                "blue",
}

# Colours come from ui/theme.py so report panels match every other card.
from ui import theme as _theme
from ui import widgets as _W

_TONE_TEXT = {k: v["fg"] for k, v in _theme.TONES.items()}
_TONE_TEXT["dim"] = _theme.C["text"]
_BODY = _theme.C["text"]


def _tone_for_heading(text: str) -> str:
    low = text.lower()
    for k, v in _VERDICT_TONE.items():
        if k in low:
            return v
    if "spread" in low or "bucket" in low:
        return "teal"
    if "missing" in low or "diagnostic" in low:
        return "amber"
    if "interpretation" in low or "notes" in low:
        return "blue"
    if "trade plan" in low:
        return "violet"
    return "dim"


# ------------ markdown splitter ---------------------------------------------
_SECTION_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.MULTILINE)


def _split_sections(md: str) -> List[Tuple[str, str]]:
    """Split markdown into (heading, body) chunks. Preamble uses '' heading."""
    md = md.strip()
    if not md:
        return []
    matches = list(_SECTION_RE.finditer(md))
    if not matches:
        return [("", md)]
    out: List[Tuple[str, str]] = []
    if matches[0].start() > 0:
        pre = md[: matches[0].start()].strip()
        if pre:
            out.append(("", pre))
    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[start:end].strip()
        out.append((heading, body))
    return out


# ------------ table parser ---------------------------------------------------
def _parse_tables(body: str) -> List[Tuple[List[str], List[List[str]], Tuple[int, int]]]:
    """Return list of (headers, rows, (start_line, end_line)) for pipe tables."""
    lines = body.splitlines()
    tables = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s\-:|]+\|?\s*$", lines[i + 1]):
            start = i
            headers = [c.strip() for c in line.strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i]:
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            tables.append((headers, rows, (start, i)))
        else:
            i += 1
    return tables


def _remove_ranges(text: str, ranges: List[Tuple[int, int]]) -> str:
    if not ranges:
        return text
    lines = text.splitlines()
    keep = [True] * len(lines)
    for a, b in ranges:
        for k in range(a, b):
            if 0 <= k < len(keep):
                keep[k] = False
    return "\n".join(l for l, k in zip(lines, keep) if k).strip()


# ------------ md → html for text runs ---------------------------------------
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITAL_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def _md_inline_to_html(s: str, accent_color: str) -> str:
    s = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    s = _BOLD_RE.sub(rf'<strong style="color:{accent_color}">\1</strong>', s)
    s = _ITAL_RE.sub(r"<em>\1</em>", s)
    s = _CODE_RE.sub(r'<code style="background:rgba(255,255,255,0.06);padding:1px 4px;">\1</code>', s)
    return s


def _body_html(body: str, tone: str) -> str:
    """Convert paragraphs + bullet lists into themed HTML."""
    accent = _TONE_TEXT.get(tone, _BODY)
    out_parts: List[str] = []
    para: List[str] = []
    bullets: List[str] = []

    def flush_para():
        if para:
            out_parts.append(
                '<p style="margin:0 0 8px 0;line-height:1.55;">'
                + " ".join(_md_inline_to_html(l, accent) for l in para) + "</p>"
            )
            para.clear()

    def flush_bullets():
        if bullets:
            items = "".join(
                f'<li style="margin:2px 0;">{_md_inline_to_html(b, accent)}</li>'
                for b in bullets
            )
            out_parts.append(
                f'<ul style="padding-left:18px;margin:0 0 8px 0;">{items}</ul>'
            )
            bullets.clear()

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_para(); flush_bullets(); continue
        if stripped.startswith(("- ", "* ", "• ")):
            flush_para()
            bullets.append(stripped[2:].strip())
        elif re.match(r"^\d+\.\s", stripped):
            flush_para()
            bullets.append(re.sub(r"^\d+\.\s", "", stripped))
        else:
            flush_bullets()
            para.append(stripped)
    flush_para(); flush_bullets()
    return "".join(out_parts)


# ------------ widget builders -----------------------------------------------
def _make_table_view(headers: List[str], rows: List[List[str]]) -> QTableView:
    model = QStandardItemModel(len(rows), len(headers))
    model.setHorizontalHeaderLabels(headers)
    for r, row in enumerate(rows):
        for c in range(len(headers)):
            val = row[c] if c < len(row) else ""
            item = QStandardItem(val)
            item.setEditable(False)
            model.setItem(r, c, item)
    view = QTableView()
    view.setModel(model)
    _W.style_table(view, embedded=True)
    view.setAlternatingRowColors(True)
    if len(headers) <= 8:
        view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    else:
        # Wide report tables (Spread Summary has ~20 columns) squashed every
        # header into "inusBotton"; size to content and scroll sideways.
        _W.fit_columns(view, max_w=200)
    view.setMinimumHeight(min(720, max(180, 34 * (len(rows) + 1) + 24)))
    return view


def _make_section_panel(heading: str, tone: str) -> Tuple[QFrame, QVBoxLayout]:
    panel, v = _W.card(tone)
    if heading:
        v.addWidget(_W.text(heading, "CardTitle", wrap=True))
    return panel, v


def _make_empty_note(text: str) -> QLabel:
    return _W.empty_note(text.strip("_ "))


def render_markdown(md_text: str, parent_layout: QVBoxLayout) -> None:
    """Append parsed markdown as themed widgets into parent_layout."""
    sections = _split_sections(md_text or "")
    if not sections:
        parent_layout.addWidget(_make_empty_note("_No report content yet._"))
        return
    for heading, body in sections:
        tone = _tone_for_heading(heading) if heading else "dim"
        panel, v = _make_section_panel(heading, tone)

        # Empty-marker heuristic: body is only italic underscore text.
        stripped = body.strip()
        if stripped.startswith("_") and stripped.endswith("_") and "\n" not in stripped:
            v.addWidget(_make_empty_note(stripped))
            parent_layout.addWidget(panel)
            continue

        tables = _parse_tables(body)
        table_ranges = [t[2] for t in tables]
        text_body = _remove_ranges(body, table_ranges)

        for headers, rows, _ in tables:
            v.addWidget(_make_table_view(headers, rows))

        if text_body.strip():
            html = _body_html(text_body, tone)
            if html.strip():
                tb = QLabel()
                tb.setTextFormat(Qt.RichText)
                tb.setWordWrap(True)
                tb.setOpenExternalLinks(True)
                tb.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
                tb.setObjectName("Body")
                tb.setText(f"<div style='color:{_BODY};'>{html}</div>")
                v.addWidget(tb)

        parent_layout.addWidget(panel)
