"""News & Events view — Context tab component.

Read-only. Reads output/news_digest.json (falls back to data CSVs).
Never mutates any pipeline artifact. Uses core.candidate_selection for any
rank ordering it needs.
"""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
        QTableWidget, QTableWidgetItem, QPushButton, QFrame, QHeaderView,
        QSplitter, QListWidget, QListWidgetItem, QSizePolicy,
    )
    from PySide6.QtCore import QUrl
except Exception:  # pragma: no cover — non-Qt envs (CI, headless tests)
    QWidget = object  # type: ignore

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = BASE_DIR / "data"

DIGEST_PATH = OUTPUT_DIR / "news_digest.json"
TOP_CAND_CSV = DATA_DIR / "top_candidate_news.csv"

FIXED_BANNER = (
    "News and filings are human-review context only. "
    "They do not change any score, rank or validation result."
)


def _read_digest() -> dict:
    if DIGEST_PATH.exists():
        try:
            return json.loads(DIGEST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _read_csv_fallback() -> pd.DataFrame:
    if TOP_CAND_CSV.exists():
        try:
            return pd.read_csv(TOP_CAND_CSV)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


class NewsEventsView(QWidget):
    """Mounted inside the Context tab."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stories: list[dict] = []
        self._filtered: list[dict] = []
        self._digest: dict = {}
        self._prev_symbols: set[str] = set()
        self._build_ui()
        self.refresh()

    # ---------- ui ----------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self); root.setContentsMargins(10, 10, 10, 10); root.setSpacing(8)

        banner = QLabel(FIXED_BANNER)
        banner.setWordWrap(True)
        banner.setStyleSheet("background:#2a1919;color:#f5d7d7;padding:8px;border-radius:6px;")
        root.addWidget(banner)

        self._kpi_row = QHBoxLayout()
        root.addLayout(self._kpi_row)

        # Filters
        filt = QHBoxLayout()
        self.f_scope = QComboBox(); self.f_scope.addItems(["All candidates", "Top 5", "Top 15"])
        self.f_type = QComboBox(); self.f_type.addItems(["All", "Official filings", "Media", "New since last run"])
        self.f_event = QComboBox(); self.f_event.addItem("All events")
        self.f_recency = QComboBox(); self.f_recency.addItems(
            ["All ages", "Recent_0_7D", "Current_8_30D", "Older_31_90D", "Stale_90DPlus", "Unknown_Date"])
        self.f_symbol = QLineEdit(); self.f_symbol.setPlaceholderText("Symbol…")
        for w in (QLabel("Scope"), self.f_scope, QLabel("Type"), self.f_type,
                  QLabel("Event"), self.f_event, QLabel("Recency"), self.f_recency,
                  QLabel("Symbol"), self.f_symbol):
            filt.addWidget(w)
        btn = QPushButton("Refresh"); btn.clicked.connect(self.refresh)
        filt.addWidget(btn); filt.addStretch(1)
        root.addLayout(filt)

        for combo in (self.f_scope, self.f_type, self.f_event, self.f_recency):
            combo.currentIndexChanged.connect(self._apply_filters)
        self.f_symbol.textChanged.connect(self._apply_filters)

        # Split: candidate summary (left) / timeline (right)
        split = QSplitter(Qt.Horizontal)
        self.tbl_candidates = QTableWidget(0, 7)
        self.tbl_candidates.setHorizontalHeaderLabels(
            ["Symbol", "Rank", "Stories", "Filings", "Latest event", "Latest age (d)", "Coverage"])
        self.tbl_candidates.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_candidates.itemSelectionChanged.connect(self._on_symbol_selected)
        split.addWidget(self.tbl_candidates)

        self.tbl_timeline = QTableWidget(0, 7)
        self.tbl_timeline.setHorizontalHeaderLabels(
            ["Published", "Event", "Headline", "Source", "Kind", "Relevance", "Open"])
        self.tbl_timeline.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_timeline.cellDoubleClicked.connect(self._open_link)
        split.addWidget(self.tbl_timeline)
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 2)
        root.addWidget(split, stretch=1)

        self._kpis: dict[str, QLabel] = {}
        for label in ("Last refresh", "Candidates", "Recent items", "Official filings",
                      "Unknown-date", "Failed sources"):
            box = QFrame(); box.setFrameShape(QFrame.StyledPanel)
            box.setStyleSheet("background:#161616;border:1px solid #333;border-radius:6px;padding:6px;")
            v = QVBoxLayout(box); v.setContentsMargins(6, 4, 6, 4); v.setSpacing(2)
            title = QLabel(label); title.setStyleSheet("color:#888;font-size:11px;")
            value = QLabel("—"); value.setStyleSheet("color:#eee;font-size:15px;font-weight:600;")
            v.addWidget(title); v.addWidget(value)
            self._kpi_row.addWidget(box)
            self._kpis[label] = value

    # ---------- data ----------
    def refresh(self) -> None:
        # Shared structured reader — same source Decision Center + Workbench use.
        from core.ui_readers import read_news_digest  # local import: Qt-safe
        self._digest = read_news_digest(OUTPUT_DIR)
        stories = list(self._digest.get("stories") or [])
        if not stories:
            df = _read_csv_fallback()
            if not df.empty:
                stories = df.to_dict(orient="records")
        self._stories = stories

        # KPIs
        counts = self._digest.get("counts", {}) or {}
        self._kpis["Last refresh"].setText(str(self._digest.get("generated_at", "—")))
        self._kpis["Candidates"].setText(str(counts.get("candidates", "—")))
        self._kpis["Recent items"].setText(str(counts.get("candidate_stories", len(stories))))
        self._kpis["Official filings"].setText(str(counts.get("official_filings", "—")))
        self._kpis["Unknown-date"].setText(str(counts.get("unknown_date", "—")))
        failed = sum(1 for h in (self._digest.get("source_health") or [])
                     if isinstance(h, dict) and h.get("Fetch_Status") == "failed")
        self._kpis["Failed sources"].setText(str(failed))

        # populate event dropdown
        events = sorted({s.get("Event_Category", "") for s in stories if s.get("Event_Category")})
        cur = self.f_event.currentText()
        self.f_event.blockSignals(True)
        self.f_event.clear(); self.f_event.addItem("All events")
        for e in events:
            self.f_event.addItem(e)
        if cur in ("All events", *events):
            self.f_event.setCurrentText(cur)
        self.f_event.blockSignals(False)

        self._apply_filters()

    def _apply_filters(self) -> None:
        rows = list(self._stories)
        scope = self.f_scope.currentText()
        if scope == "Top 5":
            rows = [r for r in rows if _to_int(r.get("Rank"), 1_000) <= 5]
        elif scope == "Top 15":
            rows = [r for r in rows if _to_int(r.get("Rank"), 1_000) <= 15]
        t = self.f_type.currentText()
        if t == "Official filings":
            rows = [r for r in rows if bool(r.get("Is_Official_Filing"))]
        elif t == "Media":
            rows = [r for r in rows if not bool(r.get("Is_Official_Filing"))]
        elif t == "New since last run":
            # Compare First_Seen against the previous *successful* refresh
            # timestamp so this works correctly across multiple same-day runs.
            cutoff = (self._digest.get("previous_successful_refresh_at")
                      or self._digest.get("last_successful_refresh_at")
                      or self._digest.get("generated_at", ""))
            rows = [r for r in rows if str(r.get("First_Seen", "")) > str(cutoff)]
        ev = self.f_event.currentText()
        if ev != "All events":
            rows = [r for r in rows if r.get("Event_Category") == ev]
        rec = self.f_recency.currentText()
        if rec != "All ages":
            rows = [r for r in rows if r.get("Recency_Bucket") == rec]
        sym = self.f_symbol.text().strip().upper()
        if sym:
            rows = [r for r in rows if sym in str(r.get("Symbol", "")).upper()]
        self._filtered = rows
        self._render_candidate_summary()
        self._render_timeline(rows)

    def _render_candidate_summary(self) -> None:
        if not self._filtered:
            self.tbl_candidates.setRowCount(0); return
        df = pd.DataFrame(self._filtered)
        df["Rank_num"] = pd.to_numeric(df.get("Rank"), errors="coerce")
        df["Age_num"] = pd.to_numeric(df.get("Age_Days"), errors="coerce")
        grp = df.groupby("Symbol", dropna=False)
        summary = grp.agg(
            Rank=("Rank_num", "min"),
            Stories=("Symbol", "count"),
            Filings=("Is_Official_Filing", lambda s: int(sum(bool(x) for x in s))),
            Latest_event=("Event_Category", "first"),
            Latest_age=("Age_num", "min"),
        ).reset_index().sort_values(["Rank", "Symbol"], na_position="last")
        self.tbl_candidates.setRowCount(len(summary))
        for i, row in enumerate(summary.itertuples(index=False)):
            for j, v in enumerate([row.Symbol, _fmt(row.Rank), row.Stories, row.Filings,
                                    row.Latest_event, _fmt(row.Latest_age), ""]):
                it = QTableWidgetItem(str(v))
                it.setFlags(it.flags() ^ Qt.ItemIsEditable)
                self.tbl_candidates.setItem(i, j, it)

    def _render_timeline(self, rows: list[dict]) -> None:
        self.tbl_timeline.setRowCount(len(rows))
        for i, r in enumerate(rows):
            kind = "Filing" if bool(r.get("Is_Official_Filing")) else "Media"
            for j, v in enumerate([
                r.get("Published_Date", ""), r.get("Event_Category", ""),
                r.get("Canonical_Title", ""), r.get("Source", ""),
                kind, r.get("Relevance_Reason", ""), r.get("URL", "")
            ]):
                it = QTableWidgetItem(str(v) if v is not None else "")
                it.setFlags(it.flags() ^ Qt.ItemIsEditable)
                self.tbl_timeline.setItem(i, j, it)

    def _on_symbol_selected(self) -> None:
        items = self.tbl_candidates.selectedItems()
        if not items:
            return
        sym = self.tbl_candidates.item(items[0].row(), 0).text()
        rows = [r for r in self._filtered if str(r.get("Symbol", "")).upper() == sym.upper()]
        self._render_timeline(rows)

    def _open_link(self, row: int, _col: int) -> None:
        item = self.tbl_timeline.item(row, 6)
        if item and item.text().startswith("http"):
            try:
                QDesktopServices.openUrl(QUrl(item.text()))
            except Exception:
                pass


def _to_int(v, default=0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
