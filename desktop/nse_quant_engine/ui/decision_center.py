"""Decision Center — native Qt Overview view.

Read-only presentation over existing output/ artifacts. Never mutates scoring,
validation, adaptive weights, portfolio-selection or history-writer outputs.
Uses core.candidate_selection as the sole authority for candidate ordering.

Sections:
    A. Top fixed banner (verdict / evidence / decision mode / regime / last run)
    B. Validation progress (raw + effective dates, matured signals, gate %)
    C. Today's changes (Top-5/Top-20 diff, gainers/losers, risk flags, regime, shadow)
    D. Top candidates (canonical Top 5-10)
    E. Review queue (items needing human attention)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import pandas as pd
from PySide6.QtCore import Qt, Signal, QThread
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QProgressBar, QSizePolicy,
)

from core.candidate_selection import (
    top_official_candidates, canonical_order,
    PRIMARY_SCORE_COL, SECONDARY_SCORE_COL,
)
from core.ui_readers import (
    read_validation_status, read_rebalance_diff, read_daily_changes,
    read_data_health, read_shadow_summary, pick_column,
)


# ---- shared factories (kept local so this module has no run_app.py cycle) ---

def _pill(text: str, tone: str = "dim") -> QLabel:
    lbl = QLabel(text); lbl.setObjectName("Pill"); lbl.setProperty("tone", tone)
    lbl.style().unpolish(lbl); lbl.style().polish(lbl)
    return lbl


def _kpi_card(title: str, value: str, tone: str = "dim", subtitle: str = "") -> QFrame:
    card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", tone)
    v = QVBoxLayout(card); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(4)
    t = QLabel(title.upper()); t.setObjectName("Sub")
    t.setStyleSheet("letter-spacing:1px;font-size:10.5px;")
    tone_color = {"teal": "#7FE0C6", "amber": "#F2B13C", "red": "#FF8597",
                  "blue": "#9CC6FF", "violet": "#C6A8FA", "green": "#7FE0A6",
                  "indigo": "#A9BCFF", "dim": "#FFFFFF"}.get(tone, "#FFFFFF")
    val = QLabel(value); val.setStyleSheet(
        f"color:{tone_color};font-size:22px;font-weight:800;background:transparent;")
    v.addWidget(t); v.addWidget(val)
    if subtitle:
        s = QLabel(subtitle); s.setObjectName("Sub"); s.setWordWrap(True)
        v.addWidget(s)
    return card


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        "color:#8A92A6;font-size:10.5px;letter-spacing:1.4px;"
        "font-weight:700;margin-top:6px;background:transparent;")
    return lbl


def _empty_note(msg: str) -> QLabel:
    lbl = QLabel(msg); lbl.setObjectName("Sub"); lbl.setWordWrap(True)
    lbl.setStyleSheet("color:#6B6F76;font-size:11.5px;font-style:italic;background:transparent;")
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


class DecisionCenterView(QWidget):
    """Decision Center — the native Overview.

    Public API expected by MainWindow (mirrors legacy `Dashboard`):
        - set_console_callback(cb)
        - refresh()
        - open_browser()
        - `view` attribute (None here; the HTML dashboard is a separate tab)
    """
    # No signals — refreshes are direct calls from MainWindow.
    def __init__(self, base: Path, out: Path):
        super().__init__()
        self.BASE = base
        self.OUT = out
        self.view = None  # for compat with self-check
        self._console_callback: Callable[[str], None] | None = None
        self._refresh_thread: QThread | None = None

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(10)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget(); self._body = QVBoxLayout(self._holder)
        self._body.setContentsMargins(0, 0, 0, 0); self._body.setSpacing(12)
        self._scroll.setWidget(self._holder)
        root.addWidget(self._scroll)

    # ---- callbacks ----
    def set_console_callback(self, cb):
        self._console_callback = cb

    def _log(self, msg: str):
        if self._console_callback:
            try: self._console_callback(msg)
            except Exception: pass

    def open_browser(self):
        p = self.OUT / "dashboard_latest.html"
        if p.exists():
            import webbrowser
            webbrowser.open(p.as_uri())

    # ---- render ----
    def _clear(self):
        while self._body.count():
            it = self._body.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def refresh(self):
        self._clear()
        OUT = self.OUT

        status   = read_validation_status(OUT)
        manifest = _read_json(OUT / "run_manifest.json")
        macro    = _read_json(OUT / "macro_context.json")
        tilt     = _read_json(OUT / "regime_tilt_report.json")
        rebal    = read_rebalance_diff(OUT)
        daily    = read_daily_changes(OUT)
        shadow_j = read_shadow_summary(OUT)
        health   = read_data_health(self.BASE)

        scores   = _read_csv(OUT / "latest_scores.csv")
        trade    = _read_csv(OUT / "trade_plan_latest.csv")
        fwd      = _read_csv(OUT / "forward_return_history.csv")
        rank_ch  = _read_csv(OUT / "rank_changes.csv")

        verdict  = status["verdict"]
        grade    = status["evidence_grade"]
        is_live  = status["is_valid_positive"]
        mode     = "LIVE" if is_live else "WATCHLIST ONLY"
        regime   = str(macro.get("regime") or "—")
        finished = manifest.get("completed_at") or "—"

        self._body.addWidget(self._banner(verdict, grade, mode, regime, finished, is_live))
        self._body.addWidget(self._section_validation_progress(status, fwd))
        self._body.addWidget(self._section_todays_changes(daily, rebal, tilt, shadow_j, health, macro))
        self._body.addWidget(self._section_top_candidates(scores, trade, rank_ch))
        self._body.addWidget(self._section_review_queue(scores, trade, rank_ch))
        self._body.addWidget(self._section_overlay_activation())
        self._body.addStretch()

    # ---------------- A. Banner ---------------------------------------------
    def _banner(self, verdict, grade, mode, regime, finished, is_live) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card")
        wrap.setProperty("accent", "green" if is_live else "amber")
        v = QVBoxLayout(wrap); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(8)

        row = QHBoxLayout(); row.setSpacing(10)
        row.addWidget(_kpi_card("Verdict", verdict, "green" if is_live else "amber"))
        row.addWidget(_kpi_card("Evidence", grade, "blue"))
        row.addWidget(_kpi_card("Decision mode", mode, "green" if is_live else "amber"))
        row.addWidget(_kpi_card("Regime", regime, "violet"))
        last_txt = str(finished)[:19].replace("T", " ") if finished and finished != "—" else "—"
        row.addWidget(_kpi_card("Last run", last_txt, "indigo"))
        v.addLayout(row)

        if not is_live:
            warn = QLabel("⚠  WATCHLIST ONLY — trade levels shown anywhere in the app "
                         "are mechanical reference levels. Do not act on them.")
            warn.setWordWrap(True)
            warn.setStyleSheet(
                "background:rgba(242,177,60,0.12); color:#F2B13C; padding:8px 12px;"
                "border-radius:8px; font-weight:600; font-size:12px;")
            v.addWidget(warn)
        return wrap

    # ---------------- B. Validation progress --------------------------------
    def _section_validation_progress(self, status: dict, fwd: pd.DataFrame) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "blue")
        v = QVBoxLayout(wrap); v.setContentsMargins(16, 12, 16, 14); v.setSpacing(8)
        v.addWidget(_section_label("Validation progress"))

        stats = status.get("stats", {}) or {}
        raw_dates  = float(stats.get("validation_dates") or 0)
        eff_dates  = float(stats.get("effective_validation_dates") or 0)

        # Gate — read from core.config if importable; else fallback to 60.
        try:
            from core import config as C
            gate = float(getattr(C, "CROSSVAL_MIN_EFFECTIVE_DATES", 60))
        except Exception:
            gate = 60.0

        matured = maturing = total = 0
        if not fwd.empty:
            f = fwd
            if "Horizon_Days" in f.columns:
                try:
                    f10 = f[pd.to_numeric(f["Horizon_Days"], errors="coerce") == 10]
                    if not f10.empty: f = f10
                except Exception:
                    pass
            total = int(len(f))
            if "Net_Forward_Return" in f.columns:
                matured = int(f["Net_Forward_Return"].notna().sum())
                maturing = int(f["Net_Forward_Return"].isna().sum())
            else:
                maturing = total

        row = QGridLayout(); row.setHorizontalSpacing(10); row.setVerticalSpacing(8)
        row.addWidget(_kpi_card("Raw dates", f"{raw_dates:.0f}", "violet",
                                "distinct signal dates on record"), 0, 0)
        row.addWidget(_kpi_card("Effective dates", f"{eff_dates:.1f}", "violet",
                                f"quality-weighted (gate ≥ {gate:.0f})"), 0, 1)
        row.addWidget(_kpi_card("Signals matured", str(matured), "teal",
                                "10d forward returns landed"), 0, 2)
        row.addWidget(_kpi_card("Awaiting maturation", str(maturing), "amber",
                                "10d forward returns pending"), 0, 3)
        v.addLayout(row)

        pct = min(100.0, (eff_dates / gate * 100.0) if gate > 0 else 0.0)
        pb_label = QLabel(f"Progress to validation gate: {eff_dates:.1f} / {gate:.0f} "
                          f"effective dates  ({pct:.0f}%)")
        pb_label.setStyleSheet("color:#B7BCC6;font-size:11.5px;background:transparent;")
        pb = QProgressBar(); pb.setRange(0, 100); pb.setValue(int(pct))
        pb.setTextVisible(False); pb.setFixedHeight(10)
        pb.setStyleSheet(
            "QProgressBar{background:rgba(255,255,255,0.06);border-radius:5px;}"
            "QProgressBar::chunk{background:#7FE0A6;border-radius:5px;}")
        v.addWidget(pb_label); v.addWidget(pb)
        return wrap

    # ---------------- C. Today's changes ------------------------------------
    def _section_todays_changes(self, daily: dict, rebal: dict,
                                tilt: dict, shadow_j: dict,
                                health: dict, macro: dict) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "indigo")
        v = QVBoxLayout(wrap); v.setContentsMargins(16, 12, 16, 14); v.setSpacing(6)
        v.addWidget(_section_label("Today's changes"))

        bullets: list[tuple[str, str]] = []

        if not daily.get("previous_snapshot_available", True):
            bullets.append(("First snapshot on record — no prior run to diff against.", "dim"))

        # Top-5 / Top-20 entrants + leavers from daily_changes.json (structured)
        t5in  = list(daily.get("top5_entries") or [])
        t5out = list(daily.get("top5_exits") or [])
        t20in = list(daily.get("top20_entries") or [])
        t20out= list(daily.get("top20_exits") or [])
        if t5in:  bullets.append((f"↑ Top-5 entrants: {', '.join(t5in)}", "green"))
        if t5out: bullets.append((f"↓ Top-5 exits: {', '.join(t5out)}", "amber"))
        if t20in: bullets.append((f"↑ Top-20 entrants: {', '.join(t20in)}", "blue"))
        if t20out:bullets.append((f"↓ Top-20 exits: {', '.join(t20out)}", "dim"))

        # Rank gainers / losers from structured daily_changes
        for r in (daily.get("largest_rank_gainers") or [])[:3]:
            bullets.append((f"Rank gainer: {r.get('Symbol')} "
                            f"({int(r.get('rank_change', 0)):+d})", "green"))
        for r in (daily.get("largest_rank_losers") or [])[:3]:
            bullets.append((f"Rank loser: {r.get('Symbol')} "
                            f"({int(r.get('rank_change', 0)):+d})", "amber"))

        # New / cleared risk flags
        for f in (daily.get("new_risk_flags") or [])[:5]:
            bullets.append((f"⚠ New risk flag: {f.get('Symbol')} — {f.get('flag')}", "amber"))
        for f in (daily.get("cleared_risk_flags") or [])[:5]:
            bullets.append((f"✓ Risk flag cleared: {f.get('Symbol')}", "green"))

        # Regime change from daily_changes (falls back to macro dict)
        rc = daily.get("regime_change")
        if rc and rc.get("from") and rc.get("to"):
            bullets.append((f"Market regime change: {rc['from']} → {rc['to']}", "violet"))

        # Rebalance headline (Top-5 basket turnover / recommendation)
        rec = rebal.get("recommendation")
        turn = rebal.get("estimated_turnover_pct")
        if rec:
            extra = f" (turnover ~{turn}%)" if turn is not None else ""
            bullets.append((f"Rebalance: {rec}{extra}", "blue"))

        # Regime tilt notes (if any)
        note = str(tilt.get("note") or tilt.get("summary") or "").strip()
        if note:
            bullets.append((f"Regime tilt: {note}", "violet"))

        # Shadow vs official (from structured summary)
        champ = shadow_j.get("champion")
        if champ and champ != "review":
            tone = "green" if champ == "official" else "amber"
            bullets.append((f"Shadow vs official: {champ} leads on filtered EV/day", tone))

        # Data-source failures
        if health.get("reds"):
            bullets.append((f"Data feed failures: {', '.join(health['reds'])}", "red"))
        if health.get("ambers"):
            bullets.append((f"Data feed degraded: {', '.join(health['ambers'])}", "amber"))

        if not bullets:
            v.addWidget(_empty_note("No material changes vs. previous run."))
        else:
            for text, tone in bullets:
                dot = {"green": "#7FE0A6", "amber": "#F2B13C", "red": "#FF8597",
                       "blue": "#9CC6FF", "violet": "#C6A8FA",
                       "indigo": "#A9BCFF", "dim": "#B7BCC6"}.get(tone, "#B7BCC6")
                lbl = QLabel(f"<span style='color:{dot};'>●</span>  {text}")
                lbl.setTextFormat(Qt.RichText); lbl.setWordWrap(True)
                lbl.setStyleSheet("color:#ECEDEE;font-size:12px;background:transparent;padding:2px 0;")
                v.addWidget(lbl)
        return wrap

    # ---------------- D. Top candidates -------------------------------------
    def _section_top_candidates(self, scores: pd.DataFrame,
                                trade: pd.DataFrame, rank_ch: pd.DataFrame) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "teal")
        v = QVBoxLayout(wrap); v.setContentsMargins(16, 12, 16, 14); v.setSpacing(8)
        v.addWidget(_section_label("Top candidates (canonical order)"))

        src = trade if not trade.empty else scores
        top = top_official_candidates(src, 10)
        if top.empty:
            v.addWidget(_empty_note("No officially eligible candidates in the latest run."))
            return wrap

        # Rank change lookup
        rc_map: dict[str, float] = {}
        if not rank_ch.empty and {"Symbol", "Rank_Change"}.issubset(rank_ch.columns):
            for r in rank_ch.itertuples():
                try: rc_map[str(r.Symbol)] = float(r.Rank_Change)
                except Exception: pass

        header_cols = ["Rank", "Δ", "Symbol", "Name", "CAS ▸", "Raw",
                       "Bucket", "RSI", "Vol", "DD", "Risk"]
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(6)
        for c, h in enumerate(header_cols):
            hl = QLabel(h); hl.setStyleSheet(
                "color:#8A92A6;font-size:10.5px;letter-spacing:.6px;font-weight:700;"
                "background:transparent;")
            grid.addWidget(hl, 0, c)

        def _fmt(v, nd=1):
            try:
                f = float(v)
                if pd.isna(f): return "—"
                return f"{f:.{nd}f}"
            except Exception:
                return "—"

        for i, (_, r) in enumerate(top.iterrows(), start=1):
            sym = str(r.get("Symbol", "?"))
            rank = r.get("Opportunity_Rank", i)
            try: rank_disp = str(int(float(rank)))
            except Exception: rank_disp = str(i)
            delta = rc_map.get(sym)
            if delta is None or (isinstance(delta, float) and pd.isna(delta)):
                delta_txt, delta_col = "—", "#6B6F76"
            else:
                d = int(delta)
                if d > 0:   delta_txt, delta_col = f"▲{d}", "#7FE0A6"
                elif d < 0: delta_txt, delta_col = f"▼{abs(d)}", "#FF8597"
                else:       delta_txt, delta_col = "•", "#6B6F76"
            cells = [
                (rank_disp, "#ECEDEE"),
                (delta_txt, delta_col),
                (sym, "#FFFFFF"),
                (str(r.get("Name", "") or "")[:32], "#B7BCC6"),
                (_fmt(r.get(PRIMARY_SCORE_COL)), "#9CC6FF"),
                (_fmt(r.get(SECONDARY_SCORE_COL)), "#6B6F76"),
                (str(r.get("Bucket", "") or "—"), "#C6A8FA"),
                (_fmt(r.get("RSI_14") if "RSI_14" in r.index else r.get("RSI")), "#ECEDEE"),
                (_fmt(r.get("Volatility_20D") if "Volatility_20D" in r.index else
                      (r.get("Volatility_60D") if "Volatility_60D" in r.index else r.get("Volatility"))), "#ECEDEE"),
                (_fmt(r.get("Current_Drawdown_60D") if "Current_Drawdown_60D" in r.index else
                      (r.get("Max_Drawdown_60D") if "Max_Drawdown_60D" in r.index else r.get("Drawdown"))), "#F2B13C"),
                (str(r.get("Risk_Flag", "") or "")[:20], "#F2B13C"),
            ]
            for c, (txt, col) in enumerate(cells):
                weight = "700" if c in (2, 4) else "500"
                size = "12.5px" if c == 4 else "11.5px"
                lbl = QLabel(txt)
                lbl.setStyleSheet(f"color:{col};font-size:{size};font-weight:{weight};"
                                  f"background:transparent;")
                grid.addWidget(lbl, i, c)
        v.addLayout(grid)
        return wrap

    # ---------------- E. Review queue ---------------------------------------
    def _section_review_queue(self, scores: pd.DataFrame, trade: pd.DataFrame,
                              rank_ch: pd.DataFrame) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "amber")
        v = QVBoxLayout(wrap); v.setContentsMargins(16, 12, 16, 14); v.setSpacing(6)
        v.addWidget(_section_label("Review queue"))

        items: list[tuple[str, str]] = []

        # 1. Earnings inside hold window (top5_event_calendar.csv Event_Risk_Flag == In_Window)
        ev = _read_csv(self.OUT / "top5_event_calendar.csv")
        if not ev.empty and "Event_Risk_Flag" in ev.columns:
            inwin = ev[ev["Event_Risk_Flag"].astype(str) == "In_Window"]
            for r in inwin.itertuples(index=False):
                sym = getattr(r, "Symbol", "?")
                items.append((f"⏰ Earnings inside hold window: {sym}", "amber"))

        # 2. New/active risk flags on Top-5
        src = trade if not trade.empty else scores
        top5 = top_official_candidates(src, 5)
        if not top5.empty and "Risk_Flag" in top5.columns:
            flagged = top5[top5["Risk_Flag"].astype(str).str.strip().ne("")
                           & top5["Risk_Flag"].notna()]
            for r in flagged.itertuples(index=False):
                items.append((f"⚠ Risk flag on Top-5: {r.Symbol} — {r.Risk_Flag}", "amber"))

        # 3. Large rank changes (>|10|)
        if not rank_ch.empty and {"Symbol", "Rank_Change"}.issubset(rank_ch.columns):
            rc = rank_ch.copy()
            rc["Rank_Change"] = pd.to_numeric(rc["Rank_Change"], errors="coerce")
            big = rc[rc["Rank_Change"].abs() >= 10]
            for r in big.itertuples():
                items.append((f"↕ Large rank move: {r.Symbol} ({int(r.Rank_Change):+d})", "blue"))

        # 4. Official / shadow disagreement — top-25 overlap (Jaccard) low
        cmp_j = read_shadow_summary(self.OUT)
        jac = cmp_j.get("jaccard_top25")
        try:
            if jac is not None and float(jac) < 0.5:
                items.append((f"⇄ Official vs shadow Top-25 overlap only {float(jac):.0%}",
                              "violet"))
        except Exception:
            pass
        if cmp_j.get("champion") == "shadow":
            items.append(("⇄ Shadow model leads official on filtered EV/day", "violet"))

        # 5. Data quality reds/amber (structured feeds dict)
        health = read_data_health(self.BASE)
        for k in health.get("reds", []):
            items.append((f"● Data feed RED: {k}", "red"))
        for k in health.get("ambers", []):
            items.append((f"● Data feed AMBER: {k}", "amber"))

        if not items:
            v.addWidget(_empty_note("Nothing needs human review right now."))
        else:
            for text, tone in items[:20]:
                dot = {"green": "#7FE0A6", "amber": "#F2B13C", "red": "#FF8597",
                       "blue": "#9CC6FF", "violet": "#C6A8FA"}.get(tone, "#B7BCC6")
                lbl = QLabel(f"<span style='color:{dot};'>●</span>  {text}")
                lbl.setTextFormat(Qt.RichText); lbl.setWordWrap(True)
                lbl.setStyleSheet("color:#ECEDEE;font-size:12px;background:transparent;padding:2px 0;")
                v.addWidget(lbl)
        return wrap

    # ---------------- F. Overlay activation strip ---------------------------
    def _section_overlay_activation(self) -> QFrame:
        wrap = QFrame(); wrap.setObjectName("Card"); wrap.setProperty("accent", "dim")
        v = QVBoxLayout(wrap); v.setContentsMargins(16, 10, 16, 12); v.setSpacing(6)
        v.addWidget(_section_label("Optional overlays (auto-refreshed each run)"))

        checklist = [
            ("data/fii_dii_daily.csv",       "FII/DII flow"),
            ("data/bulk_deals.csv",          "Bulk deals"),
            ("data/fundamentals_latest.csv", "Fundamentals"),
            ("data/earnings_calendar.csv",   "Earnings calendar"),
        ]
        row = QHBoxLayout(); row.setSpacing(8)
        for rel, label in checklist:
            p = self.BASE / rel
            if p.exists():
                age_h = (time.time() - p.stat().st_mtime) / 3600.0
                if age_h < 24: tone = "green"
                elif age_h < 24 * 7: tone = "amber"
                else: tone = "amber"
                row.addWidget(_pill(f"● {label}", tone))
            else:
                row.addWidget(_pill(f"○ {label} (missing)", "amber"))
        row.addStretch()
        v.addLayout(row)

        btn = QPushButton("🔄 Refresh optional feeds now")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("padding:6px 12px;")
        btn.clicked.connect(self._refresh_optional_feeds)
        v.addWidget(btn, alignment=Qt.AlignLeft)
        return wrap

    def _refresh_optional_feeds(self):
        self._log("[fetch] manual refresh requested — running in background")
        import sys as _sys
        base = self.BASE
        parent = self

        class _RefreshThread(QThread):
            done_signal = Signal()
            def run(self_inner):
                try:
                    if str(base) not in _sys.path:
                        _sys.path.insert(0, str(base))
                    from core.optional_data_fetchers import refresh_all
                    refresh_all(base)
                except Exception as e:
                    print(f"[fetch] manual refresh failed: {e}", flush=True)
                self_inner.done_signal.emit()

        t = _RefreshThread(self)
        t.done_signal.connect(lambda: (parent._log("[fetch] manual refresh done"),
                                       parent.refresh()))
        self._refresh_thread = t
        t.start()
