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

from core.candidate_selection import (
    canonical_order, is_eligible,
    PRIMARY_SCORE_COL, SECONDARY_SCORE_COL,
)
from core.ui_readers import (
    pick_column as _pick_col, read_news_digest, read_daily_changes,
    stories_for_symbol,
)


# ---- shared helpers (kept local; no cycle with run_app.py) ------------------

def _pill(text: str, tone: str = "dim") -> QLabel:
    lbl = QLabel(text); lbl.setObjectName("Pill"); lbl.setProperty("tone", tone)
    lbl.style().unpolish(lbl); lbl.style().polish(lbl)
    return lbl


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        "color:#8A92A6;font-size:10.5px;letter-spacing:1.4px;"
        "font-weight:700;margin-top:6px;background:transparent;")
    return lbl


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
        bar = QFrame(); bar.setObjectName("Card"); bar.setProperty("accent", "indigo")
        h = QHBoxLayout(bar); h.setContentsMargins(10, 8, 10, 8); h.setSpacing(8)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText("Search symbol or name…")
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self._apply_filters)
        h.addWidget(self.txt_search, 2)

        self.cb_universe = QComboBox(); self.cb_universe.addItems(["All universes"])
        self.cb_universe.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(self.cb_universe)

        self.cb_type = QComboBox(); self.cb_type.addItems(["Stocks + ETFs", "Stocks only", "ETFs only"])
        self.cb_type.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(self.cb_type)

        self.cb_bucket = QComboBox(); self.cb_bucket.addItems(["All buckets"])
        self.cb_bucket.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(self.cb_bucket)

        self.cb_eligible = QComboBox()
        self.cb_eligible.addItems(["All", "Eligible", "Ineligible"])
        self.cb_eligible.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(self.cb_eligible)

        self.cb_flag = QComboBox()
        self.cb_flag.addItems(["All", "Clean", "Flagged"])
        self.cb_flag.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(self.cb_flag)

        # Mode: strictly separated. Official is the pipeline authority.
        # Shadow shows shadow-only ordering (never merged into official).
        # Compare uses OFFICIAL ordering and appends Shadow_Rank / Shadow_Score.
        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["Official", "Shadow", "Compare"])
        self.cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        h.addWidget(QLabel("Mode:"))
        h.addWidget(self.cb_mode)

        self.cb_new20 = QComboBox()
        self.cb_new20.addItems(["Any", "New Top-20 entrant only"])
        self.cb_new20.currentIndexChanged.connect(self._apply_filters)
        h.addWidget(self.cb_new20)

        return bar

    # ------------- Table side ------------------------------------------------
    def _build_table_side(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(6)
        self.lbl_count = QLabel("0 candidates"); self.lbl_count.setObjectName("Sub")
        self.lbl_count.setStyleSheet("color:#8A92A6;font-size:11px;padding:2px 4px;")
        v.addWidget(self.lbl_count)

        self.table = QTableView()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        v.addWidget(self.table, 1)

        btn_row = QHBoxLayout(); btn_row.setSpacing(6)
        self.btn_copy = QPushButton("Copy selected row")
        self.btn_copy.clicked.connect(self._copy_selected_row)
        btn_row.addWidget(self.btn_copy)
        self.btn_open_scores = QPushButton("Open latest_scores.csv")
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
        self._insp_v.setContentsMargins(6, 6, 6, 6); self._insp_v.setSpacing(10)
        self._inspector_scroll.setWidget(self._insp_holder)
        placeholder = QLabel("Select a candidate to inspect.")
        placeholder.setStyleSheet("color:#6B6F76;font-size:12px;padding:16px;background:transparent;")
        self._insp_v.addWidget(placeholder); self._insp_v.addStretch()
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

        # SHADOW: keep strictly separate. Order shadow by its own score
        # (Confidence_Adjusted_Score preferred, then Final_Score). Never
        # mixed into official ordering. Symbol is the join key for Compare.
        self._df_shadow = pd.DataFrame()
        self._shadow_rank_map: dict[str, int] = {}
        self._shadow_score_map: dict[str, float] = {}
        if not shadow.empty and "Symbol" in shadow.columns:
            score_col = None
            for c in (PRIMARY_SCORE_COL, SECONDARY_SCORE_COL, "Opportunity_Score"):
                if c in shadow.columns:
                    score_col = c; break
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
        self._reload_combo(self.cb_bucket, "All buckets",
                           sorted(df["Bucket"].dropna().astype(str).unique().tolist()) if "Bucket" in df.columns else [])

        self._apply_filters()

    def _on_mode_changed(self):
        # Switching Official ↔ Shadow swaps the base dataset entirely.
        self._apply_mode_source()
        df = self._df_all
        self._reload_combo(self.cb_universe, "All universes",
                           sorted(df["Universe"].dropna().astype(str).unique().tolist()) if "Universe" in df.columns else [])
        self._reload_combo(self.cb_bucket, "All buckets",
                           sorted(df["Bucket"].dropna().astype(str).unique().tolist()) if "Bucket" in df.columns else [])
        self._apply_filters()

    def _apply_mode_source(self):
        mode = self.cb_mode.currentText()
        if mode == "Shadow":
            self._df_all = self._df_shadow.copy().reset_index(drop=True)
        else:  # Official OR Compare — both use official as the base ordering
            self._df_all = self._df_official.copy().reset_index(drop=True)
...
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
            "Rank", "ΔRank", "Symbol", "Name", "Universe", "Bucket",
            "Adj Score", "Raw Score", "Confidence", "RSI", "Vol", "DD",
            "Risk", "News", "Event",
        ]
        if mode == "Compare":
            headers += ["Shadow Rank", "Shadow Score"]

        model = QStandardItemModel(len(df), len(headers))
        model.setHorizontalHeaderLabels(headers)

        col_map = {
            "Rank":       "Opportunity_Rank",
            "Symbol":     "Symbol",
            "Name":       "Name",
            "Universe":   "Universe",
            "Bucket":     "Bucket",
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
                            txt = ("▲" if di > 0 else ("▼" if di < 0 else "•")) + str(abs(di))
                    it = QStandardItem(txt)
                elif h == "Shadow Rank":
                    sr = self._shadow_rank_map.get(sym)
                    it = QStandardItem("" if sr is None else str(int(sr)))
                elif h == "Shadow Score":
                    ss = self._shadow_score_map.get(sym)
                    it = QStandardItem(_fmt(ss, 1) if ss is not None else "")
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
                it.setEditable(False)
                model.setItem(r, c, it)

        self.table.setModel(model)
        self.table.selectionModel().selectionChanged.connect(
            lambda *_: self._on_selection_changed())
        # Preselect first row so inspector isn't empty
        if len(df) > 0:
            self.table.selectRow(0)

    def _selected_index(self) -> int | None:
        sel = self.table.selectionModel()
        if sel is None: return None
        rows = sel.selectedRows()
        if not rows: return None
        return rows[0].row()

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
            lbl = QLabel("Select a candidate to inspect.")
            lbl.setStyleSheet("color:#6B6F76;font-size:12px;padding:16px;background:transparent;")
            self._insp_v.addWidget(lbl); self._insp_v.addStretch(); return

        sym = str(row.get("Symbol", "?"))
        name = str(row.get("Name", "") or "")
        elig = str(row.get("Opportunity_Eligible", "") or "")
        bucket = str(row.get("Bucket", "") or "")

        # Header card
        head = QFrame(); head.setObjectName("Card")
        head.setProperty("accent", "teal" if elig.strip().lower() == "yes" else "amber")
        hv = QVBoxLayout(head); hv.setContentsMargins(14, 12, 14, 12); hv.setSpacing(4)
        row_top = QHBoxLayout()
        sym_lbl = QLabel(sym); sym_lbl.setStyleSheet("font-size:18px;font-weight:800;color:#fff;background:transparent;")
        row_top.addWidget(sym_lbl); row_top.addStretch()
        row_top.addWidget(_pill(bucket or "—", "teal" if "Top" in bucket else "blue"))
        row_top.addWidget(_pill("ELIGIBLE" if elig.strip().lower() == "yes" else "INELIGIBLE",
                                "green" if elig.strip().lower() == "yes" else "amber"))
        hv.addLayout(row_top)
        if name:
            nl = QLabel(name); nl.setStyleSheet("color:#8A92A6;font-size:12px;background:transparent;")
            nl.setWordWrap(True); hv.addWidget(nl)
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
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "blue")
        v = QVBoxLayout(wrap); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(6)
        v.addWidget(_section_label("Ranking explanation (horizontal bars)"))
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
        note.setStyleSheet("color:#B7BCC6;font-size:11.5px;background:transparent;margin-top:4px;")
        v.addWidget(note)
        disc = QLabel("Displayed for interpretation only. No score is computed here.")
        disc.setStyleSheet("color:#6B6F76;font-size:10.5px;font-style:italic;background:transparent;")
        v.addWidget(disc)
        return wrap

    def _hbar(self, label: str, value: float | None) -> QWidget:
        w = QWidget(); h = QHBoxLayout(w); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(8)
        lbl = QLabel(label); lbl.setFixedWidth(160)
        lbl.setStyleSheet("color:#B7BCC6;font-size:11.5px;background:transparent;")
        h.addWidget(lbl)
        bar = QFrame(); bar.setFixedHeight(10); bar.setMinimumWidth(180)
        bar.setStyleSheet("background:rgba(255,255,255,0.06);border-radius:5px;")
        h.addWidget(bar, 1)
        v_txt = QLabel(_fmt(value, 1) if value is not None else "—")
        v_txt.setFixedWidth(50); v_txt.setAlignment(Qt.AlignRight)
        v_txt.setStyleSheet("color:#ECEDEE;font-size:11.5px;background:transparent;")
        h.addWidget(v_txt)
        # Overlay fill inside bar
        if value is not None:
            clipped = max(-100.0, min(100.0, float(value)))
            pct = (clipped + 100.0) / 200.0 if clipped < 0 or clipped > 100 else clipped / 100.0
            pct = max(0.0, min(1.0, pct))
            fill = QFrame(bar); fill.setStyleSheet("background:#7FE0A6;border-radius:5px;")
            def _resize(evt, bar=bar, fill=fill, pct=pct):
                fill.setGeometry(0, 0, int(bar.width() * pct), bar.height())
            bar.resizeEvent = _resize  # type: ignore
        return w

    def _card_price_ladder(self, sym: str, row: pd.Series) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "violet")
        v = QVBoxLayout(wrap); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(6)
        v.addWidget(_section_label("Mechanical price ladder"))

        # Prefer trade_plan_latest.csv row for the symbol
        trade = _read_csv(self.OUT / "trade_plan_latest.csv")
        tr = None
        if not trade.empty and "Symbol" in trade.columns:
            m = trade[trade["Symbol"].astype(str) == sym]
            if not m.empty: tr = m.iloc[0]
        src = tr if tr is not None else row

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
            "Current":     _g("Current_Price") or _g("Close"),
            "Target 1":    _g("Target_1"),
            "Target 2":    _g("Target_2"),
        }
        # Grid layout for level chips
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(4)
        for i, (k, v_) in enumerate(levels.items()):
            grid.addWidget(QLabel(k), 0, i)
            val = QLabel(_fmt(v_, 2) if v_ is not None else "—")
            val.setStyleSheet("color:#ECEDEE;font-weight:700;font-size:13px;background:transparent;")
            grid.addWidget(val, 1, i)
            grid.itemAtPosition(0, i).widget().setStyleSheet(
                "color:#6B6F76;font-size:10.5px;letter-spacing:.4px;background:transparent;")
        v.addLayout(grid)

        # Warn about mechanical-only unless validation positive
        vstat = _read_json(self.OUT / "validation_status.json")
        if str(vstat.get("verdict", "")) != "Validation Positive":
            warn = QLabel("Mechanical reference levels only — validation is not positive.")
            warn.setStyleSheet("color:#F2B13C;font-size:11px;font-weight:600;background:transparent;margin-top:4px;")
            warn.setWordWrap(True); v.addWidget(warn)
        return wrap

    def _card_history(self, sym: str) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "indigo")
        v = QVBoxLayout(wrap); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(6)
        v.addWidget(_section_label("History"))

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
            kl = QLabel(k); kl.setStyleSheet("color:#6B6F76;font-size:10.5px;letter-spacing:.4px;background:transparent;")
            vl = QLabel(v_); vl.setStyleSheet("color:#ECEDEE;font-size:13px;font-weight:700;background:transparent;")
            grid.addWidget(kl, 0, col); grid.addWidget(vl, 1, col)
        _kv(0, "Current rank",  str(cur_rank) if cur_rank is not None else "—")
        _kv(1, "Previous rank", str(prev_rank) if prev_rank is not None else "—")
        _kv(2, "Best recent",   str(best_rank) if best_rank is not None else "—")
        _kv(3, "Days in Top 25",str(days_top25))
        v.addLayout(grid)
        return wrap

    def _card_risk_context(self, sym: str, row: pd.Series) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "amber")
        v = QVBoxLayout(wrap); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(6)
        v.addWidget(_section_label("Risk & context"))

        bullets: list[tuple[str, str]] = []

        rf = str(row.get("Risk_Flag", "") or "").strip()
        if rf: bullets.append((f"Risk flag: {rf}", "amber"))

        # Earnings inside hold window?
        ev = _read_csv(self.OUT / "top5_event_calendar.csv")
        if not ev.empty and "Symbol" in ev.columns:
            m = ev[ev["Symbol"].astype(str) == sym]
            if not m.empty and "Event_Risk_Flag" in m.columns:
                fl = str(m.iloc[0]["Event_Risk_Flag"])
                if fl == "In_Window":
                    bullets.append(("Earnings inside proposed holding window", "amber"))

        # Sector context
        sec = _read_csv(self.OUT / "top5_sector_context.csv")
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
            v.addWidget(QLabel("No additional risk / context data available."))
        for text, tone in bullets:
            dot = {"green": "#7FE0A6", "amber": "#F2B13C", "red": "#FF8597",
                   "blue": "#9CC6FF", "violet": "#C6A8FA", "teal": "#7FE0C6"}.get(tone, "#B7BCC6")
            lbl = QLabel(f"<span style='color:{dot};'>●</span>  {text}")
            lbl.setTextFormat(Qt.RichText); lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#ECEDEE;font-size:11.5px;background:transparent;padding:1px 0;")
            v.addWidget(lbl)
        return wrap

    def _card_news_reserved(self, sym: str) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "dim")
        v = QVBoxLayout(wrap); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(6)
        v.addWidget(_section_label("News & filings (context only)"))
        digest = read_news_digest(self.OUT)
        stories = stories_for_symbol(digest, sym)
        rows = len(stories)
        lbl = QLabel(f"{rows} recent headline(s) referencing {sym}." if rows
                     else f"No recent headlines linked to {sym}.")
        lbl.setStyleSheet("color:#B7BCC6;font-size:11.5px;background:transparent;")
        lbl.setWordWrap(True); v.addWidget(lbl)
        disc = QLabel("Context only — no effect on ranking or validation.")
        disc.setStyleSheet("color:#6B6F76;font-size:10.5px;font-style:italic;background:transparent;")
        v.addWidget(disc)
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
