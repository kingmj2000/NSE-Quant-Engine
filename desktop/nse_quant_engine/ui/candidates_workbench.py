"""Candidates Workbench — searchable candidate table + inspector.

Read-only over latest_scores.csv (+ trade_plan_latest.csv, score history,
event calendar). Never mutates scoring, validation, portfolio-selection,
adaptive-weight or history-writer outputs.
Ordering always flows through core.candidate_selection.canonical_order.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtGui import QStandardItemModel, QStandardItem, QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLineEdit, QComboBox,
    QLabel, QTableView, QFrame, QScrollArea, QGridLayout, QSizePolicy,
    QPushButton, QAbstractItemView,
)

from core import output_paths as OP
from ui import theme
from ui import widgets as W
from core.candidate_selection import (
    canonical_order, is_eligible,
    PRIMARY_SCORE_COL, SECONDARY_SCORE_COL,
)
from core.ui_readers import (
    pick_column as _pick_col, read_news_digest, read_daily_changes,
    stories_for_symbol, is_risk_flag, risk_flag_mask,
)


# ---- shared helpers (kept local; no cycle with run_app.py) ------------------

def _pill(text: str, tone: str = "dim") -> QLabel:
    return W.pill(text, tone)


def _section_label(text: str) -> QLabel:
    return W.section_label(text)


# Table sorting: QStandardItemModel sorts on this role. Numeric columns carry a
# float (so 10 sorts after 9), text columns their lower-cased text, and "Rank"
# carries the canonical position, so sorting by Rank restores official order.
SORT_ROLE = Qt.UserRole + 1
ROW_ROLE = Qt.UserRole          # canonical position of the row in _df_view
_NUMERIC_HEADERS = {"ΔRank", "Adj Score", "Raw Score", "Confidence", "RSI", "Vol",
                    "DD", "News", "Shadow Rank", "Shadow Score"}
_MISSING_SORT = -1e18           # blanks sink to the bottom of a descending sort


def _as_float(v: Any) -> float | None:
    try:
        f = float(v)
    except Exception:
        return None
    return None if pd.isna(f) else f


def _read_csv(p: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(p) if p.exists() else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _read_json(p: Path) -> dict:
    try:
        if not p.exists(): return {}
        return json.loads(p.read_text(encoding="utf-8").replace(": NaN", ": null"))
    except Exception:
        return {}


# ---- Model helpers ----------------------------------------------------------

DISPLAY_COLS = [
    "Rank", "ΔRank", "Symbol", "Name", "Universe", "Bucket",
    PRIMARY_SCORE_COL, SECONDARY_SCORE_COL, "Confidence_Score",
    "RSI", "Volatility", "Drawdown", "Risk_Flag", "News_Count", "Event",
]


def _fmt(v: Any, nd: int = 2) -> str:
    try:
        f = float(v)
        if pd.isna(f): return ""
        return f"{f:.{nd}f}"
    except Exception:
        if v is None: return ""
        if isinstance(v, float) and pd.isna(v): return ""
        return str(v)


class CandidatesWorkbench(QWidget):
    """Public API expected by MainWindow:
           - refresh()
    """

    def __init__(self, base: Path, out: Path):
        super().__init__()
        self.BASE = base
        self.OUT = out
        self._df_all = pd.DataFrame()   # ordered canonical view (all rows)
        self._df_view = pd.DataFrame()  # after filters
        self._rank_change: dict[str, float] = {}
        self._trade_syms: set[str] = set()

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(8)
        root.addWidget(self._build_filter_bar())

        self._splitter = QSplitter(Qt.Horizontal)
        self._splitter.addWidget(self._build_table_side())
        self._splitter.addWidget(self._build_inspector_side())
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setSizes([720, 520])
        root.addWidget(self._splitter, 1)

    # ------------- Filter bar ------------------------------------------------
    def _build_filter_bar(self) -> QWidget:
        bar, outer = W.card(margins=12)
        h = QHBoxLayout(); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
        outer.addLayout(h)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Symbol or company name")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._apply_filters)
        h.addWidget(W.labeled("Search", self.txt_search), 3)

        def _combo(items):
            cb = QComboBox(); cb.addItems(items)
            cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(9)
            W.polish_combo(cb)
            return cb

        self.cb_universe = _combo(["All universes"])
        self.cb_universe.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(W.labeled("Universe", self.cb_universe), 2)

        self.cb_type = _combo(["Stocks + ETFs", "Stocks only", "ETFs only"])
        self.cb_type.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(W.labeled("Instrument", self.cb_type), 2)

        self.cb_bucket = _combo(["All raw score buckets"])
        self.cb_bucket.setToolTip("Raw Score Bucket — diagnostic only; official standing "
                                  "is rank + Confidence_Adjusted_Score.")

        self.cb_bucket.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(W.labeled("Raw score bucket", self.cb_bucket), 2)

        self.cb_eligible = _combo(["All", "Eligible", "Ineligible"])
        self.cb_eligible.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(W.labeled("Eligibility", self.cb_eligible), 1)

        self.cb_flag = _combo(["All", "Clean", "Flagged"])
        self.cb_flag.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(W.labeled("Risk flag", self.cb_flag), 1)

        self.cb_new20 = _combo(["Any", "New Top-20 entrant only"])
        self.cb_new20.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(W.labeled("Top-20", self.cb_new20), 1)

        # Mode: strictly separated. Official is the pipeline authority.
        # Shadow shows shadow-only ordering (never merged into official).
        # Compare uses OFFICIAL ordering and appends Shadow_Rank / Shadow_Score.
        self.cb_mode = _combo(["Official", "Shadow", "Compare"])
        self.cb_mode.setToolTip("Official = pipeline authority. Shadow = diagnostic "
                                "ordering only. Compare = official order + shadow columns.")
        self.cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        h.addWidget(W.labeled("Ranking", self.cb_mode), 1)

        return bar

    # ------------- Table side ------------------------------------------------
    def _build_table_side(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        self.lbl_count = QLabel("0 candidates"); self.lbl_count.setObjectName("Meta")
        v.addWidget(self.lbl_count)

        self.table = QTableView()
        self.table.horizontalHeader().setStretchLastSection(True)
        W.style_table(self.table, row_height=32)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        # Start in canonical (Rank) order; header clicks re-sort numerically.
        self.table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        v.addWidget(self.table, 1)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        self.btn_copy = QPushButton("⧉  Copy selected row")
        self.btn_copy.setToolTip("Copy the selected candidate as tab-separated values")
        self.btn_copy.clicked.connect(self._copy_selected_row)
        btn_row.addWidget(self.btn_copy)
        self.btn_open_scores = QPushButton("↗  Show latest_scores.csv")
        self.btn_open_scores.clicked.connect(
            lambda: self._reveal(self.OUT / "latest_scores.csv"))
        btn_row.addWidget(self.btn_open_scores)
        btn_row.addStretch()
        v.addLayout(btn_row)
        return w

    def _build_inspector_side(self) -> QWidget:
        self._inspector_scroll = QScrollArea()
        self._inspector_scroll.setWidgetResizable(True)
        self._inspector_scroll.setFrameShape(QFrame.NoFrame)
        self._insp_holder = QWidget()
        self._insp_v = QVBoxLayout(self._insp_holder)
        self._insp_v.setContentsMargins(2, 0, 6, 0); self._insp_v.setSpacing(theme.GAP)
        self._inspector_scroll.setWidget(self._insp_holder)
        self._insp_v.addWidget(W.empty_note("Select a candidate to inspect.")); self._insp_v.addStretch()
        return self._inspector_scroll

    # ------------- Refresh entry point --------------------------------------
    def refresh(self):
        scores = _read_csv(self.OUT / "latest_scores.csv")
        trade  = _read_csv(self.OUT / "trade_plan_latest.csv")
        rc     = _read_csv(self.OUT / "rank_changes.csv")
        shadow = _read_csv(self.OUT / "latest_scores_v4_shadow.csv")

        # Rank-change lookup (official only)
        self._rank_change.clear()
        if not rc.empty and {"Symbol", "Rank_Change"}.issubset(rc.columns):
            for r in rc.itertuples():
                try: self._rank_change[str(r.Symbol)] = float(r.Rank_Change)
                except Exception: pass

        self._trade_syms = set(trade["Symbol"].astype(str).tolist()) if not trade.empty and "Symbol" in trade.columns else set()

        # OFFICIAL: canonical order retains ALL rows.
        self._df_official = canonical_order(scores, eligible_only=False).reset_index(drop=True)

        # SHADOW: keep strictly separate. Order shadow by ITS OWN score, which is
        # exactly `V4_Confidence_Adjusted_Score`. No fallback chain: the shadow
        # file is derived from the official file and can inherit official score
        # columns, so name-guessing here would display the OFFICIAL score as the
        # shadow score and make the two look identical. Symbol is the join key.
        SHADOW_SCORE_COL = "V4_Confidence_Adjusted_Score"
        self._df_shadow = pd.DataFrame()
        self._shadow_rank_map: dict[str, int] = {}
        self._shadow_score_map: dict[str, float] = {}
        if not shadow.empty and "Symbol" in shadow.columns:
            score_col = SHADOW_SCORE_COL if SHADOW_SCORE_COL in shadow.columns else None
            if score_col is not None:
                s = shadow.copy()
                s["_score"] = pd.to_numeric(s[score_col], errors="coerce")
                s = s.sort_values(["_score", "Symbol"], ascending=[False, True],
                                  na_position="last").reset_index(drop=True)
                for i, r in s.iterrows():
                    sym = str(r["Symbol"])
                    self._shadow_rank_map[sym] = i + 1
                    try: self._shadow_score_map[sym] = float(r["_score"])
                    except Exception: pass
                self._df_shadow = s.drop(columns=["_score"])
            else:
                self._df_shadow = shadow.copy()

        self._apply_mode_source()

        # Populate filter combos from currently-active df
        df = self._df_all
        self._reload_combo(self.cb_universe, "All universes",
                           sorted(df["Universe"].dropna().astype(str).unique().tolist()) if "Universe" in df.columns else [])
        self._reload_combo(self.cb_bucket, "All raw score buckets",
                           sorted(df["Bucket"].dropna().astype(str).unique().tolist()) if "Bucket" in df.columns else [])

        self._apply_filters()

    def focus_symbol(self, symbol: str) -> None:
        """Show one symbol (used by the Overview links): search for it and select it."""
        sym = str(symbol or "").strip()
        if not sym:
            return
        self.txt_search.setText(sym)          # triggers _apply_filters + selects row 0
        try:
            self.txt_search.setFocus()
        except Exception:
            pass

    def focus_search(self) -> None:
        try:
            self.txt_search.setFocus()
            self.txt_search.selectAll()
        except Exception:
            pass

    def _on_mode_changed(self):
        # Switching Official ↔ Shadow swaps the base dataset entirely.
        self._apply_mode_source()
        df = self._df_all
        self._reload_combo(self.cb_universe, "All universes",
                           sorted(df["Universe"].dropna().astype(str).unique().tolist()) if "Universe" in df.columns else [])
        self._reload_combo(self.cb_bucket, "All raw score buckets",
                           sorted(df["Bucket"].dropna().astype(str).unique().tolist()) if "Bucket" in df.columns else [])
        self._apply_filters()

    def _apply_mode_source(self):
        mode = self.cb_mode.currentText()
        if mode == "Shadow":
            self._df_all = self._df_shadow.copy().reset_index(drop=True)
        else:  # Official OR Compare — both use official as the base ordering
            self._df_all = self._df_official.copy().reset_index(drop=True)

    # ------------- Filter-combo repopulation --------------------------------
    @staticmethod
    def _reload_combo(combo: QComboBox, placeholder: str, values: list[str]) -> None:
        """Repopulate `combo` with `[placeholder] + values`, keeping the user's
        current choice when it still exists in the new data.

        Index 0 is ALWAYS the placeholder: `_apply_filters` treats
        `currentIndex() > 0` as "a real filter is selected", so inserting the
        placeholder anywhere else would silently turn "All universes" into a
        filter on the literal string "All universes" and empty the table.

        Signals are blocked during the rebuild. `currentIndexChanged` is wired to
        `_apply_filters`, and clearing a combo emits it — so repopulating without
        blocking would fire the filter chain several times mid-rebuild, against a
        half-populated widget. Both callers invoke `_apply_filters()` themselves
        immediately afterwards, so nothing is lost by staying quiet here.
        """
        if combo is None:
            return
        previous = combo.currentText()
        items = [placeholder] + [str(v) for v in (values or [])]
        was_blocked = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItems(items)
            if previous in items:
                combo.setCurrentIndex(items.index(previous))
            else:
                combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(was_blocked)

    # ------------- Filters --------------------------------------------------
    def _apply_filters(self):
        df = self._df_all
        if df.empty:
            self._df_view = df
            self.table.setModel(QStandardItemModel(0, 0))
            self.lbl_count.setText("0 candidates")
            self._render_inspector(None)
            return

        m = pd.Series([True] * len(df), index=df.index)

        q = self.txt_search.text().strip().lower()
        if q:
            sym = df["Symbol"].astype(str).str.lower() if "Symbol" in df.columns else pd.Series("", index=df.index)
            nam = df["Name"].astype(str).str.lower()   if "Name"   in df.columns else pd.Series("", index=df.index)
            m &= sym.str.contains(q, na=False) | nam.str.contains(q, na=False)

        if self.cb_universe.currentIndex() > 0 and "Universe" in df.columns:
            m &= df["Universe"].astype(str) == self.cb_universe.currentText()

        if "Universe" in df.columns:
            t = self.cb_type.currentText()
            if t == "Stocks only":
                m &= ~df["Universe"].astype(str).str.contains("ETF", case=False, na=False)
            elif t == "ETFs only":
                m &= df["Universe"].astype(str).str.contains("ETF", case=False, na=False)

        if self.cb_bucket.currentIndex() > 0 and "Bucket" in df.columns:
            m &= df["Bucket"].astype(str) == self.cb_bucket.currentText()

        elig = self.cb_eligible.currentText()
        if elig != "All" and "Opportunity_Eligible" in df.columns:
            elig_mask = is_eligible(df)
            m &= elig_mask if elig == "Eligible" else ~elig_mask

        flag = self.cb_flag.currentText()
        if flag != "All" and "Risk_Flag" in df.columns:
            has_flag = risk_flag_mask(df["Risk_Flag"])   # "Clean" is not a flag
            m &= has_flag if flag == "Flagged" else ~has_flag

        if self.cb_new20.currentIndex() > 0:
            daily = read_daily_changes(self.OUT)
            added20 = set(daily.get("top20_entries") or [])
            if added20 and "Symbol" in df.columns:
                m &= df["Symbol"].astype(str).isin(added20)
            else:
                m &= False

        self._df_view = df[m].reset_index(drop=True)
        self._populate_table(self._df_view)
        mode = self.cb_mode.currentText()
        suffix = {"Official": "official ranking (CAS)",
                  "Shadow":   "SHADOW ranking — informational, not authoritative",
                  "Compare":  "official ranking + shadow columns"}.get(mode, "")
        self.lbl_count.setText(f"{len(self._df_view):,} of {len(self._df_all):,} — {suffix}")

    # ------------- Table population ----------------------------------------
    def _populate_table(self, df: pd.DataFrame):
        mode = self.cb_mode.currentText()
        headers = [
            "Rank", "ΔRank", "Symbol", "Name", "Universe", "Raw Score Bucket",
            "Adj Score", "Raw Score", "Confidence", "RSI", "Vol", "DD",
            "Risk", "News", "Event",
        ]
        if mode == "Compare":
            headers += ["Shadow Rank", "Shadow Score"]

        model = QStandardItemModel(len(df), len(headers))
        model.setHorizontalHeaderLabels(headers)
        model.setSortRole(SORT_ROLE)

        col_map = {
            "Rank":       "Opportunity_Rank",
            "Symbol":     "Symbol",
            "Name":       "Name",
            "Universe":   "Universe",
            "Bucket":     "Bucket",
            # The header is "Raw Score Bucket"; without this key the column
            # looked up a non-existent "Raw Score Bucket" field and was blank.
            "Raw Score Bucket": "Bucket",
            "Adj Score":  PRIMARY_SCORE_COL,
            "Raw Score":  SECONDARY_SCORE_COL,
            "Confidence": _pick_col(df, "Confidence") or "Confidence_Score",
            "RSI":        _pick_col(df, "RSI") or "RSI_14",
            "Vol":        _pick_col(df, "Volatility") or "Volatility_20D",
            "DD":         _pick_col(df, "Drawdown") or "Current_Drawdown_60D",
            "Risk":       "Risk_Flag",
            "News":       _pick_col(df, "News_Count") or "News_Count",
            "Event":      _pick_col(df, "Event") or "Event",
        }

        for r, (_, row) in enumerate(df.iterrows()):
            sym = str(row.get("Symbol", ""))
            for c, h in enumerate(headers):
                sort_val: Any = None
                if h == "ΔRank":
                    # In Shadow mode there is no official ΔRank — leave blank.
                    if mode == "Shadow":
                        txt = ""
                    else:
                        d = self._rank_change.get(sym)
                        if d is None or (isinstance(d, float) and pd.isna(d)):
                            txt = ""
                        else:
                            di = int(d)
                            txt = ("▲" + str(di)) if di > 0 else (("▼" + str(abs(di))) if di < 0 else "•")
                            sort_val = float(di)
                    it = QStandardItem(txt)
                elif h == "Shadow Rank":
                    sr = self._shadow_rank_map.get(sym)
                    it = QStandardItem("" if sr is None else str(int(sr)))
                    sort_val = None if sr is None else float(sr)
                elif h == "Shadow Score":
                    ss = self._shadow_score_map.get(sym)
                    it = QStandardItem(_fmt(ss, 1) if ss is not None else "")
                    sort_val = ss
                else:
                    src = col_map.get(h, h)
                    val = row.get(src) if src in row.index else None
                    if h in ("Adj Score", "Raw Score", "Confidence"):
                        txt = _fmt(val, 1)
                    elif h == "Rank":
                        try: txt = str(int(float(val)))
                        except Exception: txt = ""
                    elif h in ("RSI", "Vol", "DD"):
                        txt = _fmt(val, 2)
                    else:
                        txt = "" if val is None or (isinstance(val, float) and pd.isna(val)) else str(val)
                    it = QStandardItem(txt)
                    if h in _NUMERIC_HEADERS:
                        sort_val = _as_float(val)
                # Sort key for this cell (see SORT_ROLE).
                if h == "Rank":
                    it.setData(float(r), SORT_ROLE)        # canonical position
                    it.setData(r, ROW_ROLE)                # survives re-sorting
                elif h in _NUMERIC_HEADERS:
                    it.setData(_MISSING_SORT if sort_val is None else float(sort_val), SORT_ROLE)
                else:
                    it.setData(txt.lower(), SORT_ROLE)
                if h in _NUMERIC_HEADERS or h == "Rank":
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                it.setEditable(False)
                model.setItem(r, c, it)

        self.table.setModel(model)
        # Apply the header's current sort (Rank ascending = canonical order on
        # first load; the user's chosen column afterwards).
        hdr = self.table.horizontalHeader()
        if 0 <= hdr.sortIndicatorSection() < len(headers):
            self.table.sortByColumn(hdr.sortIndicatorSection(), hdr.sortIndicatorOrder())
        W.fit_columns(self.table, max_w=240)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self._on_selection_changed())
        # Preselect first row so inspector isn't empty
        if len(df) > 0:
            self.table.selectRow(0)

    def _selected_index(self) -> int | None:
        """Position of the selected row in self._df_view.

        The view row is NOT that position once the user sorts by a header: the
        model reorders its rows in place, so inspecting `_df_view.iloc[view_row]`
        showed a different candidate than the highlighted one. Every row carries
        its canonical position in ROW_ROLE on the first column instead.
        """
        sel = self.table.selectionModel()
        if sel is None: return None
        rows = sel.selectedRows()
        if not rows: return None
        view_row = rows[0].row()
        model = self.table.model()
        try:
            item = model.item(view_row, 0)
            pos = item.data(ROW_ROLE) if item is not None else None
            if pos is not None:
                return int(pos)
        except Exception:
            pass
        return view_row

    def _on_selection_changed(self):
        idx = self._selected_index()
        if idx is None or idx >= len(self._df_view):
            self._render_inspector(None); return
        self._render_inspector(self._df_view.iloc[idx])

    # ------------- Inspector -------------------------------------------------
    def _clear_inspector(self):
        while self._insp_v.count():
            it = self._insp_v.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def _render_inspector(self, row: pd.Series | None):
        self._clear_inspector()
        if row is None:
            self._insp_v.addWidget(W.empty_note("Select a candidate to inspect."))
            self._insp_v.addStretch(); return

        sym = str(row.get("Symbol", "?"))
        name = str(row.get("Name", "") or "")
        elig = str(row.get("Opportunity_Eligible", "") or "")
        bucket = str(row.get("Bucket", "") or "")

        # Header card
        is_elig = elig.strip().lower() == "yes"
        head, hv = W.card(None if is_elig else "amber", variant="hero", spacing=4)
        row_top = QHBoxLayout(); row_top.setSpacing(6)
        row_top.addWidget(W.text(sym, "Hero")); row_top.addStretch()
        row_top.addWidget(_pill(bucket or "—", "teal" if "Top" in bucket else "blue"))
        row_top.addWidget(_pill("Eligible" if elig.strip().lower() == "yes" else "Ineligible",
                                "green" if elig.strip().lower() == "yes" else "amber"))
        hv.addLayout(row_top)
        if name:
            hv.addWidget(W.text(name, "Meta", wrap=True))
        self._insp_v.addWidget(head)

        # Ranking-explanation card (horizontal bars)
        self._insp_v.addWidget(self._card_ranking_explanation(row))

        # Mechanical price ladder
        self._insp_v.addWidget(self._card_price_ladder(sym, row))

        # History (from score_history / rank_changes)
        self._insp_v.addWidget(self._card_history(sym))

        # Risk & context
        self._insp_v.addWidget(self._card_risk_context(sym, row))

        # News reserved panel
        self._insp_v.addWidget(self._card_news_reserved(sym))

        self._insp_v.addStretch()

    def _card_ranking_explanation(self, row: pd.Series) -> QFrame:
        wrap, v = W.card(spacing=7)
        v.addWidget(W.text("What drives the score", "CardTitle"))
        components = [
            ("Momentum",           "Momentum_Score"),
            ("Trend",              "Trend_Score"),
            ("Relative strength",  "Relative_Strength_Score"),
            ("Risk (lower=better)","Risk_Score"),
            ("Liquidity",          "Liquidity_Score"),
            ("ETF quality",        "ETF_Quality_Score"),
        ]
        # Confidence adjustment (multiplier / bar-scaled to [-100..100])
        conf = row.get("Confidence_Score")
        try: conf_txt = f"{float(conf):.1f}"
        except Exception: conf_txt = "—"

        for label, key in components:
            val = row.get(key)
            try: fv = float(val)
            except Exception: fv = None
            v.addWidget(self._hbar(label, fv))

        note = QLabel(
            f"Confidence-Adjusted Score = <b>{_fmt(row.get(PRIMARY_SCORE_COL), 1)}</b> "
            f"&nbsp;·&nbsp; Raw Final_Score = {_fmt(row.get(SECONDARY_SCORE_COL), 1)} "
            f"&nbsp;·&nbsp; Confidence = {conf_txt}")
        note.setTextFormat(Qt.RichText); note.setWordWrap(True)
        note.setObjectName("Meta")
        v.addWidget(note)
        v.addWidget(W.empty_note("Displayed for interpretation only. No score is computed here."))
        return wrap

    def _hbar(self, label: str, value: float | None) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(10)
        lbl = W.text(label, "Meta"); lbl.setFixedWidth(150)
        h.addWidget(lbl)
        bar = QFrame(); bar.setObjectName("BarTrack"); bar.setFixedHeight(8); bar.setMinimumWidth(140)
        h.addWidget(bar, 1)
        v_txt = W.text(_fmt(value, 1) if value is not None else "—", "Value")
        v_txt.setFixedWidth(46); v_txt.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        h.addWidget(v_txt)
        # Overlay fill inside bar
        if value is not None:
            clipped = max(-100.0, min(100.0, float(value)))
            pct = (clipped + 100.0) / 200.0 if clipped < 0 or clipped > 100 else clipped / 100.0
            pct = max(0.0, min(1.0, pct))
            fill = QFrame(bar); fill.setObjectName("BarFill")
            def _resize(evt, bar=bar, fill=fill, pct=pct):
                fill.setGeometry(0, 0, int(bar.width() * pct), bar.height())
            bar.resizeEvent = _resize  # type: ignore
        return w

    def _card_price_ladder(self, sym: str, row: pd.Series) -> QFrame:
        wrap, v = W.card(spacing=8)
        v.addWidget(W.text("Mechanical price ladder", "CardTitle"))

        # Prefer trade_plan_latest.csv row for the symbol
        trade = _read_csv(self.OUT / "trade_plan_latest.csv")
        tr = None
        if not trade.empty and "Symbol" in trade.columns:
            m = trade[trade["Symbol"].astype(str) == sym]
            if not m.empty: tr = m.iloc[0]
        src = tr if tr is not None else row

        def _price_from(r):
            for key in ("Current_Price", "Close", "Price"):
                try:
                    f = float(r.get(key)) if key in r.index else None
                    if f is not None and not pd.isna(f):
                        return f
                except Exception:
                    continue
            return None

        def _g(key):
            val = src.get(key) if key in src.index else None
            try:
                f = float(val)
                return None if pd.isna(f) else f
            except Exception:
                return None

        levels = {
            "Stop":        _g("Stop_Loss"),
            "Buy zone lo": _g("Buy_Zone_Low"),
            "Buy zone hi": _g("Buy_Zone_High"),
            # trade_plan_latest.csv carries no price column, so "Current" was
            # always blank; fall back to the scores row (Close / Price).
            "Current":     _g("Current_Price") or _g("Close") or _price_from(row),
            "Target 1":    _g("Target_1"),
            "Target 2":    _g("Target_2"),
        }
        # Grid layout for level chips
        level_tone = {"Stop": "amber", "Target 1": "teal", "Target 2": "teal", "Current": "blue"}
        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(8)
        for i, (k, v_) in enumerate(levels.items()):
            grid.addWidget(W.stat(k, _fmt(v_, 2) if v_ is not None else "—",
                                  level_tone.get(k)), i // 3, i % 3)
        v.addLayout(grid)

        # Warn about mechanical-only unless validation positive
        vstat = _read_json(self.OUT / "validation_status.json")
        if str(vstat.get("verdict", "")) != "Validation Positive":
            v.addWidget(W.banner("Mechanical reference levels only — validation is not positive.",
                                 "amber"))
        return wrap

    def _card_history(self, sym: str) -> QFrame:
        wrap, v = W.card(spacing=8)
        v.addWidget(W.text("Rank history", "CardTitle"))

        sh = _read_csv(self.OUT / "score_history.csv")
        rc = _read_csv(self.OUT / "rank_changes.csv")

        cur_rank = prev_rank = best_rank = None
        days_top25 = 0
        if not sh.empty and "Symbol" in sh.columns and "Opportunity_Rank" in sh.columns:
            m = sh[sh["Symbol"].astype(str) == sym].copy()
            if not m.empty and "Date" in m.columns:
                m = m.sort_values("Date")
                ranks = pd.to_numeric(m["Opportunity_Rank"], errors="coerce")
                valid = ranks.dropna()
                if not valid.empty:
                    cur_rank  = int(valid.iloc[-1])
                    prev_rank = int(valid.iloc[-2]) if len(valid) >= 2 else None
                    best_rank = int(valid.min())
                    days_top25 = int((valid <= 25).sum())

        # Rank change from rank_changes.csv when available
        if cur_rank is None and not rc.empty and "Symbol" in rc.columns:
            m = rc[rc["Symbol"].astype(str) == sym]
            if not m.empty and "New_Rank" in m.columns:
                try: cur_rank = int(m.iloc[0]["New_Rank"])
                except Exception: pass
                try: prev_rank = int(m.iloc[0]["Old_Rank"])
                except Exception: pass

        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(4)
        def _kv(col, k, v_):
            grid.addWidget(W.stat(k, v_), 0, col)
        _kv(0, "Current rank",  str(cur_rank) if cur_rank is not None else "—")
        _kv(1, "Previous rank", str(prev_rank) if prev_rank is not None else "—")
        _kv(2, "Best recent",   str(best_rank) if best_rank is not None else "—")
        _kv(3, "Days in Top 25",str(days_top25))
        v.addLayout(grid)
        return wrap

    def _card_risk_context(self, sym: str, row: pd.Series) -> QFrame:
        wrap, v = W.card(spacing=6)
        v.addWidget(W.text("Risk & context", "CardTitle"))

        bullets: list[tuple[str, str]] = []

        rf = str(row.get("Risk_Flag", "") or "").strip()
        if is_risk_flag(rf): bullets.append((f"Risk flag: {rf}", "amber"))

        # Earnings inside hold window?
        ev = _read_csv(self.OUT / OP.TOP5_EVENTS_CSV)
        if not ev.empty and "Symbol" in ev.columns:
            m = ev[ev["Symbol"].astype(str) == sym]
            if not m.empty and "Event_Risk_Flag" in m.columns:
                fl = str(m.iloc[0]["Event_Risk_Flag"])
                if fl == "In_Window":
                    bullets.append(("Earnings inside proposed holding window", "amber"))

        # Sector context
        sec = _read_csv(self.OUT / OP.TOP5_SECTOR_CONTEXT_CSV)
        if not sec.empty and "Symbol" in sec.columns:
            m = sec[sec["Symbol"].astype(str) == sym]
            if not m.empty:
                sector = str(m.iloc[0].get("Sector", "") or "")
                if sector:
                    bullets.append((f"Sector: {sector}", "blue"))

        # Institutional flow
        inst = _read_csv(self.OUT / "top5_institutional_flow.csv")
        if not inst.empty and "Symbol" in inst.columns:
            m = inst[inst["Symbol"].astype(str) == sym]
            if not m.empty:
                for c in ("FII_Net", "DII_Net", "Institutional_Flow_Score"):
                    if c in m.columns:
                        try: fv = float(m.iloc[0][c])
                        except Exception: fv = None
                        if fv is not None:
                            bullets.append((f"{c}: {fv:,.2f}", "violet"))

        # Expected value
        ev_df = _read_csv(self.OUT / "top5_expected_value.csv")
        if not ev_df.empty and "Symbol" in ev_df.columns:
            m = ev_df[ev_df["Symbol"].astype(str) == sym]
            if not m.empty and "Expected_Value_%" in m.columns:
                try: fv = float(m.iloc[0]["Expected_Value_%"])
                except Exception: fv = None
                if fv is not None:
                    bullets.append((f"Expected value: {fv:.2f}%", "teal"))

        if not bullets:
            v.addWidget(W.empty_note("No additional risk / context data available."))
        elif any(t == "amber" for _, t in bullets):
            wrap.setProperty("accent", "amber"); W.repolish(wrap)
        for text, tone in bullets:
            v.addWidget(W.dot_line(text, tone))
        return wrap

    def _card_news_reserved(self, sym: str) -> QFrame:
        wrap, v = W.card(spacing=6)
        v.addWidget(W.text("News & filings", "CardTitle"))
        digest = read_news_digest(self.OUT)
        stories = stories_for_symbol(digest, sym)
        rows = len(stories)
        lbl = QLabel(f"{rows} recent headline(s) referencing {sym}." if rows
                     else f"No recent headlines linked to {sym}.")
        lbl.setObjectName("Meta")
        lbl.setWordWrap(True); v.addWidget(lbl)
        v.addWidget(W.empty_note("Context only — no effect on ranking or validation."))
        return wrap

    # ------------- Utility actions ------------------------------------------
    def _copy_selected_row(self):
        idx = self._selected_index()
        if idx is None or idx >= len(self._df_view): return
        row = self._df_view.iloc[idx]
        cells = [str(row.get(c, "")) if row.get(c) is not None else "" for c in self._df_view.columns]
        text = "\t".join(cells)
        QGuiApplication.clipboard().setText(text)

    def _reveal(self, path: Path):
        import subprocess, sys as _sys
        if not path.exists(): return
        try:
            if _sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif _sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except Exception:
            pass
