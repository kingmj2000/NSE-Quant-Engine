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
from PySide6.QtCore import Qt, Signal, QObject, QThread
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame,
    QScrollArea, QPushButton, QProgressBar, QSizePolicy,
)

from ui import theme
from ui import widgets as W

from core import output_paths as OP
from core.candidate_selection import (
    top_official_candidates, canonical_order,
    PRIMARY_SCORE_COL, SECONDARY_SCORE_COL,
)
from core.ui_readers import (
    read_validation_status, read_rebalance_diff, read_daily_changes,
    read_data_health, read_shadow_summary, pick_column,
    is_risk_flag, risk_flag_mask, candidate_source,
)


# ---- shared factories (thin wrappers over ui.widgets; no run_app.py cycle) ---

def _pill(text: str, tone: str = "dim") -> QLabel:
    return W.pill(text, tone)


def _kpi_card(title: str, value: str, tone: str = "dim", subtitle: str = "") -> QFrame:
    return W.kpi_card(title, value, tone, subtitle)


def _section_label(text: str) -> QLabel:
    return W.section_label(text)


def _empty_note(msg: str) -> QLabel:
    return W.empty_note(msg)


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


class _RefreshBridge(QObject):
    """Carries "worker finished" from a plain Python thread to the GUI thread.

    Defined once at module scope and owned by the widget. Qt delivers a signal
    emitted from a non-Qt thread via a queued connection, so the slot runs on the
    GUI thread where touching widgets is safe.
    """
    done = Signal()


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
        self._open_symbol_cb: Callable[[str], None] | None = None
        self._refresh_thread = None          # plain threading.Thread, not QThread
        self._refresh_busy = False
        self._refresh_bridge = _RefreshBridge()
        self._refresh_bridge.done.connect(self._on_refresh_finished)

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(10)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget(); self._body = QVBoxLayout(self._holder)
        self._body.setContentsMargins(0, 0, 4, 0); self._body.setSpacing(theme.GAP)
        self._scroll.setWidget(self._holder)
        root.addWidget(self._scroll)
        # Section widgets of the current render, re-placed (not rebuilt) when
        # the view crosses the one-column breakpoint.
        self._sections: dict[str, QWidget] = {}
        self._grid_host: QWidget | None = None
        self._one_col: bool | None = None

    # ---- callbacks ----
    def set_console_callback(self, cb):
        self._console_callback = cb

    def set_open_symbol_callback(self, cb):
        """MainWindow hook: clicking a Top-candidates symbol opens it in Candidates."""
        self._open_symbol_cb = cb

    def _open_symbol(self, sym: str) -> None:
        if self._open_symbol_cb:
            try: self._open_symbol_cb(sym)
            except Exception as exc: self._log(f"[ui] open symbol failed: {exc}")

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
        self._sections = {}
        self._grid_host = None
        self._one_col = None

    ONE_COLUMN_BELOW = 1060

    def _place_sections(self, one_col: bool) -> None:
        """Two-column dashboard grid; a single column on narrow windows."""
        if not self._sections:
            return
        if self._grid_host is not None and one_col == self._one_col:
            return
        old_host = self._grid_host
        self._one_col = one_col
        host = QWidget(); grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.GAP); grid.setVerticalSpacing(theme.GAP)
        sec = self._sections
        # Verdict -> what to look at (top candidates) -> evidence + feeds ->
        # what changed + what needs a human.
        if one_col:
            order = ("banner", "top", "progress", "overlays", "review", "changes")
            for i, k in enumerate(order):
                grid.addWidget(sec[k], i, 0)
        else:
            grid.addWidget(sec["banner"],   0, 0, 1, 2)
            grid.addWidget(sec["top"],      1, 0, 1, 2)
            grid.addWidget(sec["progress"], 2, 0)
            grid.addWidget(sec["overlays"], 2, 1)
            grid.addWidget(sec["changes"],  3, 0)
            grid.addWidget(sec["review"],   3, 1)
            grid.setColumnStretch(0, 3); grid.setColumnStretch(1, 2)
        self._grid_host = host
        self._body.insertWidget(0, host)
        for w in sec.values():
            w.show()      # reparented widgets come back hidden; show explicitly
        if old_host is not None:
            # Sections were moved into the new grid above, so the old host is
            # empty and safe to drop.
            self._body.removeWidget(old_host)
            old_host.deleteLater()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            self._place_sections(self.width() < self.ONE_COLUMN_BELOW)
        except Exception as exc:
            print(f"[ui] overview re-layout failed: {type(exc).__name__}: {exc}")

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

        self._sections = {
            "banner":   self._banner(verdict, grade, mode, regime, finished, is_live),
            "progress": self._section_validation_progress(status, fwd),
            "changes":  self._section_todays_changes(daily, rebal, tilt, shadow_j, health, macro),
            "top":      self._section_top_candidates(scores, trade, rank_ch),
            "review":   self._section_review_queue(scores, trade, rank_ch),
            "overlays": self._section_overlay_activation(),
        }
        self._body.addStretch()
        self._place_sections(self.width() < self.ONE_COLUMN_BELOW)

    # ---------------- A. Banner ---------------------------------------------
    def _banner(self, verdict, grade, mode, regime, finished, is_live) -> QFrame:
        tone = "green" if is_live else "amber"
        wrap, v = W.card(tone, variant="hero", spacing=10, margins=20)

        top = QHBoxLayout(); top.setSpacing(24)
        left = QVBoxLayout(); left.setSpacing(4)
        left.addWidget(W.text("Validation verdict", "Meta"))
        left.addWidget(W.text(verdict, "Hero", tone=None, wrap=True))
        left.addWidget(W.text(f"Evidence grade: {grade}", "Meta", wrap=True))
        top.addLayout(left, 3)

        last_txt = str(finished)[:19].replace("T", " ") if finished and finished != "—" else "—"
        stats = QGridLayout(); stats.setHorizontalSpacing(28); stats.setVerticalSpacing(10)
        stats.addWidget(W.text("Decision mode", "Faint"), 0, 0)
        stats.addWidget(W.pill("Live" if mode == "LIVE" else "Watchlist only", tone), 1, 0, Qt.AlignLeft)
        stats.addWidget(W.text("Market regime", "Faint"), 0, 1)
        stats.addWidget(W.text(regime, "Value", tone="violet"), 1, 1)
        stats.addWidget(W.text("Last run", "Faint"), 0, 2)
        stats.addWidget(W.text(last_txt, "Value"), 1, 2)
        top.addLayout(stats, 2)
        v.addLayout(top)

        if not is_live:
            v.addWidget(W.banner(
                "Watchlist only — trade levels shown anywhere in the app are mechanical "
                "reference levels. Do not act on them.", "amber"))
        return wrap

    # ---------------- B. Validation progress --------------------------------
    def _section_validation_progress(self, status: dict, fwd: pd.DataFrame) -> QFrame:
        wrap, v = W.card(spacing=10)
        v.addWidget(W.text("Validation progress", "CardTitle"))

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
        unmatchable = 0
        # Pending signals are in forward_return_missing_signals.csv, not NaN rows
        # of forward_return_history.csv — see read_maturation_progress.
        # NOTE: `OUT` is a local of refresh(); this is a different method, so it
        # must come from self. Referencing the bare name raised NameError, and a
        # bare `except: pass` swallowed it — leaving every counter at 0 while the
        # dashboard, reading the same files directly, showed 11,647 matured.
        try:
            from core.ui_readers import read_maturation_progress
            _mp = read_maturation_progress(self.OUT, horizon=10)
            matured, maturing = _mp["matured"], _mp["pending"]
            unmatchable, total = _mp["unmatchable"], _mp["total"]
        except Exception as exc:
            # Never fail silently again: a zeroed counter must be distinguishable
            # from a counter that could not be computed.
            print(f"[ui] maturation progress unavailable: {type(exc).__name__}: {exc}")

        pct = min(100.0, (eff_dates / gate * 100.0) if gate > 0 else 0.0)
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(W.text(f"{eff_dates:.1f} of {gate:.0f} effective dates", "MetricSmall"))
        head.addStretch()
        head.addWidget(W.pill(f"{pct:.0f}% to gate", "green" if pct >= 100 else "violet"))
        v.addLayout(head)
        pb = QProgressBar(); pb.setRange(0, 100); pb.setValue(int(pct))
        pb.setTextVisible(False); pb.setFixedHeight(8)
        pb.setToolTip(f"Progress to validation gate: {eff_dates:.1f} / {gate:.0f} effective dates")
        v.addWidget(pb)

        row = QGridLayout(); row.setHorizontalSpacing(10); row.setVerticalSpacing(8)
        tiles = (
            ("Raw dates", f"{raw_dates:.0f}", None, "distinct schema-v2 signal dates on record"),
            ("Effective dates", f"{eff_dates:.1f}", "violet", f"quality-weighted (gate ≥ {gate:.0f})"),
            ("Signals matured", f"{matured:,}", "teal", "10d forward returns landed"),
            ("Awaiting maturation", f"{maturing:,}", "amber", "still inside the 10d window"),
        )
        for i, (t_, val, tone, sub) in enumerate(tiles):
            tf, tl = W.tile()
            tl.addWidget(W.text(t_, "Faint"))
            tl.addWidget(W.text(val, "MetricSmall", tone=tone))
            tl.addWidget(W.text(sub, "Faint", wrap=True))
            row.addWidget(tf, i // 2, i % 2)
        v.addLayout(row)
        if unmatchable:
            v.addWidget(W.text(
                f"{unmatchable:,} signal(s) could not be matched to a price "
                f"and are excluded from the {total:,} total — usually symbols "
                f"no longer in the current universe.", "Faint", wrap=True))
        v.addStretch()
        return wrap

    # ---------------- C. Today's changes ------------------------------------
    def _section_todays_changes(self, daily: dict, rebal: dict,
                                tilt: dict, shadow_j: dict,
                                health: dict, macro: dict) -> QFrame:
        wrap, v = W.card(spacing=6)
        v.addWidget(W.text("Today's changes", "CardTitle"))

        # (group, text, tone) — same facts as before, grouped so the card scans.
        items: list[tuple[str, str, str]] = []
        RANK, RISK, MARKET, FEEDS = "Ranking", "Risk flags", "Market & engine", "Data feeds"

        if not daily.get("previous_snapshot_available", True):
            items.append((MARKET, "First snapshot on record — no prior run to diff against.", "dim"))

        # Top-5 / Top-20 entrants + leavers from daily_changes.json (structured)
        t5in  = list(daily.get("top5_entries") or [])
        t5out = list(daily.get("top5_exits") or [])
        t20in = list(daily.get("top20_entries") or [])
        t20out= list(daily.get("top20_exits") or [])
        if t5in:  items.append((RANK, f"↑ Top-5 entrants: {', '.join(t5in)}", "green"))
        if t5out: items.append((RANK, f"↓ Top-5 exits: {', '.join(t5out)}", "amber"))
        if t20in: items.append((RANK, f"↑ Top-20 entrants: {', '.join(t20in)}", "blue"))
        if t20out:items.append((RANK, f"↓ Top-20 exits: {', '.join(t20out)}", "dim"))

        # Rank gainers / losers from structured daily_changes
        gain = [f"{r.get('Symbol')} ({int(r.get('rank_change', 0)):+d})"
                for r in (daily.get("largest_rank_gainers") or [])[:3]]
        lose = [f"{r.get('Symbol')} ({int(r.get('rank_change', 0)):+d})"
                for r in (daily.get("largest_rank_losers") or [])[:3]]
        if gain: items.append((RANK, f"▲ Biggest rank gains: {', '.join(gain)}", "green"))
        if lose: items.append((RANK, f"▼ Biggest rank drops: {', '.join(lose)}", "amber"))

        # New / cleared risk flags
        new_flags = list(daily.get("new_risk_flags") or [])
        cleared = list(daily.get("cleared_risk_flags") or [])
        for f in new_flags[:5]:
            items.append((RISK, f"⚠ {f.get('Symbol')} — {f.get('flag')}", "amber"))
        if len(new_flags) > 5:
            items.append((RISK, f"+{len(new_flags) - 5} more new flags — see Candidates "
                                f"(Risk flag = Flagged)", "dim"))
        if cleared:
            shown = ", ".join(str(f.get("Symbol")) for f in cleared[:8])
            more = f" +{len(cleared) - 8} more" if len(cleared) > 8 else ""
            items.append((RISK, f"✓ Cleared: {shown}{more}", "green"))

        # Regime change from daily_changes (falls back to macro dict)
        rc = daily.get("regime_change")
        if rc and rc.get("from") and rc.get("to"):
            items.append((MARKET, f"Market regime change: {rc['from']} → {rc['to']}", "violet"))

        # Rebalance headline (Top-5 basket turnover / recommendation)
        rec = rebal.get("recommendation")
        turn = rebal.get("estimated_turnover_pct")
        if rec:
            extra = f" (turnover ~{turn}%)" if turn is not None else ""
            items.append((MARKET, f"Rebalance: {rec}{extra}", "blue"))

        # Regime tilt notes (if any)
        note = str(tilt.get("note") or tilt.get("summary") or "").strip()
        if note:
            items.append((MARKET, f"Regime tilt: {note}", "violet"))

        # Shadow vs official (from structured summary)
        lead = shadow_j.get("experimental_leader")
        if lead and lead != "review":
            tone = "green" if lead == "official" else "amber"
            note = ("shadow currently ahead — manual review suggested"
                    if lead == "shadow" else "official still ahead")
            items.append((MARKET, f"Shadow vs official (experimental): {note}", tone))

        # Data-source failures
        if health.get("reds"):
            items.append((FEEDS, f"Failing: {', '.join(health['reds'])}", "red"))
        if health.get("ambers"):
            items.append((FEEDS, f"Degraded: {', '.join(health['ambers'])}", "amber"))

        if not items:
            v.addWidget(_empty_note("No material changes vs. previous run."))
        else:
            for group in (RANK, RISK, MARKET, FEEDS):
                rows = [(t, tone) for g, t, tone in items if g == group]
                if not rows:
                    continue
                v.addSpacing(4)
                v.addWidget(W.text(group, "Faint"))
                for text, tone in rows:
                    v.addWidget(W.dot_line(text, tone))
        v.addStretch()
        return wrap

    # ---------------- D. Top candidates -------------------------------------
    def _section_top_candidates(self, scores: pd.DataFrame,
                                trade: pd.DataFrame, rank_ch: pd.DataFrame) -> QFrame:
        wrap, v = W.card(spacing=10)
        hdr = QHBoxLayout()
        hdr.addWidget(W.text("Top candidates", "CardTitle"))
        hdr.addWidget(W.text("official order · Confidence_Adjusted_Score · click a symbol to inspect", "Faint"))
        hdr.addStretch()
        v.addLayout(hdr)

        src = candidate_source(trade, scores)
        top = top_official_candidates(src, 10)
        if top.empty:
            v.addWidget(_empty_note(
                "No officially eligible candidates in the latest run. Check the Candidates "
                "page with Eligibility = All to see why rows were excluded."))
            return wrap

        # Rank change lookup
        rc_map: dict[str, float] = {}
        if not rank_ch.empty and {"Symbol", "Rank_Change"}.issubset(rank_ch.columns):
            for r in rank_ch.itertuples():
                try: rc_map[str(r.Symbol)] = float(r.Rank_Change)
                except Exception: pass

        header_cols = ["Rank", "Δ", "Symbol", "Name", "CAS ▸", "Raw",
                       "Bucket", "RSI", "Vol", "DD", "Risk"]
        grid = QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(7)
        for c, h in enumerate(header_cols):
            grid.addWidget(W.text(h, "Faint"), 0, c)
        grid.addWidget(W.divider(), 1, 0, 1, len(header_cols))

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
                delta_txt, delta_tone = "—", None
            else:
                d = int(delta)
                if d > 0:   delta_txt, delta_tone = f"▲{d}", "green"
                elif d < 0: delta_txt, delta_tone = f"▼{abs(d)}", "red"
                else:       delta_txt, delta_tone = "•", None
            # (text, role, tone) — roles/tones come from ui/theme.py
            cells = [
                (rank_disp, "Body", None),
                (delta_txt, "Value" if delta_tone else "Faint", delta_tone),
                (sym, "Value", None),
                (str(r.get("Name", "") or "")[:32], "Meta", None),
                (_fmt(r.get(PRIMARY_SCORE_COL)), "Value", "blue"),
                (_fmt(r.get(SECONDARY_SCORE_COL)), "Faint", None),
                (str(r.get("Bucket", "") or "—"), "Meta", None),
                (_fmt(r.get("RSI_14") if "RSI_14" in r.index else r.get("RSI")), "Body", None),
                (_fmt(r.get("Volatility_20D") if "Volatility_20D" in r.index else
                      (r.get("Volatility_60D") if "Volatility_60D" in r.index else r.get("Volatility"))), "Body", None),
                (_fmt(r.get("Current_Drawdown_60D") if "Current_Drawdown_60D" in r.index else
                      (r.get("Max_Drawdown_60D") if "Max_Drawdown_60D" in r.index else r.get("Drawdown"))), "Body", "amber"),
                (str(r.get("Risk_Flag", "") or "")[:20], "Meta",
                 "amber" if is_risk_flag(r.get("Risk_Flag")) else None),
            ]
            for c, (txt, role, tone) in enumerate(cells):
                if c == 2 and txt and txt != "?":
                    # Symbol is a link: click to inspect it on the Candidates page.
                    lbl = QLabel(f"<a href='{txt}' style='color:{theme.tone_fg('blue')};"
                                 f"text-decoration:none;'>{txt}</a>")
                    lbl.setObjectName("Value")
                    lbl.setTextFormat(Qt.RichText)
                    lbl.setCursor(Qt.PointingHandCursor)
                    lbl.setToolTip(f"Inspect {txt} on the Candidates page")
                    lbl.linkActivated.connect(self._open_symbol)
                    grid.addWidget(lbl, i + 1, c)
                    continue
                lbl = W.text(txt, role, tone=tone)
                if tone and role == "Meta":
                    lbl.setObjectName("Value")
                    W.repolish(lbl)
                grid.addWidget(lbl, i + 1, c)
        grid.setColumnStretch(3, 1)
        v.addLayout(grid)
        return wrap

    # ---------------- E. Review queue ---------------------------------------
    def _section_review_queue(self, scores: pd.DataFrame, trade: pd.DataFrame,
                              rank_ch: pd.DataFrame) -> QFrame:
        wrap, v = W.card(spacing=6)
        v.addWidget(W.text("Review queue", "CardTitle"))

        items: list[tuple[str, str]] = []

        # 1. Earnings inside hold window (top5_events.csv Event_Risk_Flag == In_Window)
        ev = _read_csv(self.OUT / OP.TOP5_EVENTS_CSV)
        if not ev.empty and "Event_Risk_Flag" in ev.columns:
            inwin = ev[ev["Event_Risk_Flag"].astype(str) == "In_Window"]
            for r in inwin.itertuples(index=False):
                sym = getattr(r, "Symbol", "?")
                items.append((f"⏰ Earnings inside hold window: {sym}", "amber"))

        # 2. New/active risk flags on Top-5
        src = candidate_source(trade, scores)
        top5 = top_official_candidates(src, 5)
        if not top5.empty and "Risk_Flag" in top5.columns:
            flagged = top5[risk_flag_mask(top5["Risk_Flag"])]   # "Clean" is not a flag
            for r in flagged.itertuples(index=False):
                items.append((f"⚠ Risk flag on Top-5: {r.Symbol} — {r.Risk_Flag}", "amber"))

        # 3. Large rank changes (>|10|)
        if not rank_ch.empty and {"Symbol", "Rank_Change"}.issubset(rank_ch.columns):
            rc = rank_ch.copy()
            rc["Rank_Change"] = pd.to_numeric(rc["Rank_Change"], errors="coerce")
            big = rc[rc["Rank_Change"].abs() >= 10]
            # One summary line, not one line per symbol: a busy day has hundreds
            # of >=10-place moves, and listing them pushed data-feed failures
            # past the 20-item cap so they were never shown.
            if not big.empty:
                top3 = big.reindex(big["Rank_Change"].abs().sort_values(ascending=False).index).head(3)
                biggest = ", ".join(f"{r.Symbol} ({int(r.Rank_Change):+d})" for r in top3.itertuples())
                n = len(big)
                items.append((f"↕ {n} large rank move{'s' if n != 1 else ''} (≥10 places) — "
                              f"biggest: {biggest}", "blue"))

        # 4. Official / shadow disagreement — top-25 overlap (Jaccard) low
        cmp_j = read_shadow_summary(self.OUT)
        jac = cmp_j.get("jaccard_top25")
        try:
            if jac is not None and float(jac) < 0.5:
                items.append((f"⇄ Official vs shadow Top-25 overlap only {float(jac):.0%}",
                              "violet"))
        except Exception:
            pass
        if cmp_j.get("experimental_leader") == "shadow":
            items.append(("⇄ Shadow currently ahead (experimental) — manual review suggested",
                          "violet"))

        # 5. Data quality reds/amber (structured feeds dict)
        health = read_data_health(self.BASE)
        for k in health.get("reds", []):
            items.append((f"● Data feed RED: {k}", "red"))
        for k in health.get("ambers", []):
            items.append((f"● Data feed AMBER: {k}", "amber"))

        if items:
            # The amber border marks "something needs a human"; an empty queue
            # stays a neutral card.
            wrap.setProperty("accent", "amber"); W.repolish(wrap)
        if not items:
            v.addWidget(_empty_note("Nothing needs human review right now."))
        else:
            # Most severe first: red, then amber, then the informational rest.
            order = {"red": 0, "amber": 1}
            items = sorted(items, key=lambda it: order.get(it[1], 2))
            for text, tone in items[:20]:
                v.addWidget(W.dot_line(text, tone))
            if len(items) > 20:
                v.addWidget(W.text(f"+{len(items) - 20} more", "Faint"))
        v.addStretch()
        return wrap

    # ---------------- F. Overlay activation strip ---------------------------
    def _section_overlay_activation(self) -> QFrame:
        wrap, v = W.card(spacing=8)
        # Header row: title left, refresh action right — the action sits with
        # the thing it refreshes instead of trailing the list.
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(W.text("Optional context feeds", "CardTitle"))
        head.addStretch()
        btn = QPushButton("⟳  Refresh now")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip("Re-pull FII/DII, bulk deals, fundamentals and earnings now "
                       "(runs in the background; ignores cache freshness)")
        btn.clicked.connect(self._refresh_optional_feeds)
        head.addWidget(btn)
        v.addLayout(head)
        v.addWidget(W.text("Auto-refreshed each run. Context only — never a score input.",
                           "Faint", wrap=True))

        checklist = [
            ("data/fii_dii_daily.csv",       "FII/DII flow"),
            ("data/bulk_deals.csv",          "Bulk deals"),
            ("data/fundamentals_latest.csv", "Fundamentals"),
            ("data/earnings_calendar.csv",   "Earnings calendar"),
        ]
        row = QGridLayout(); row.setHorizontalSpacing(8); row.setVerticalSpacing(8)
        for i, (rel, label) in enumerate(checklist):
            p = self.BASE / rel
            if p.exists():
                age_h = (time.time() - p.stat().st_mtime) / 3600.0
                if age_h < 24: tone = "green"
                elif age_h < 24 * 7: tone = "amber"
                else: tone = "amber"
                row.addWidget(_pill(f"● {label}", tone), i // 2, i % 2, Qt.AlignLeft)
            else:
                row.addWidget(_pill(f"○ {label} (missing)", "amber"), i // 2, i % 2, Qt.AlignLeft)
        v.addLayout(row)
        v.addStretch()
        return wrap

    def _refresh_optional_feeds(self):
        """Run the optional-feed fetchers off the GUI thread.

        WHY NOT QThread
        ---------------
        This used a QThread subclass. Two lifetime hazards came with it, and both
        appear in last_crash.log as "QThread: Destroyed while thread is still
        running", heap corruption (0xc0000374), then an access violation in
        unrelated code such as pathlib.read_text:

          * `finished -> deleteLater` destroys the C++ object while `self` still
            holds the Python wrapper, so any later isRunning() on that wrapper
            touches freed memory.
          * app exit, or a second click, could drop a live thread.

        A plain `threading.Thread` has no C++ object to destroy, so the whole
        class of fault disappears. This work is network + file IO and touches no
        Qt object, so it never needed a QThread. Completion is marshalled back to
        the GUI thread through a persistent signal bridge.
        """
        if getattr(self, "_refresh_busy", False):
            self._log("[fetch] a manual refresh is already running — ignoring this click")
            return

        import sys as _sys
        import threading

        base = self.BASE
        if getattr(self, "_refresh_bridge", None) is None:
            self._refresh_bridge = _RefreshBridge()
            self._refresh_bridge.done.connect(self._on_refresh_finished)
        self._refresh_busy = True

        def _work() -> None:
            try:
                if str(base) not in _sys.path:
                    _sys.path.insert(0, str(base))
                # force=True: a human pressed the button. A manual request must
                # override cache freshness; the window exists to stop redundant
                # AUTOMATIC fetches, not to ignore a deliberate one.
                from core.optional_data_fetchers import refresh_all
                refresh_all(base, force=True)
            except Exception as exc:
                print(f"[fetch] manual refresh failed: {type(exc).__name__}: {exc}",
                      flush=True)
            finally:
                # Emitting a Qt signal from a non-Qt thread is safe; the slot runs
                # on the GUI thread. Never call refresh() directly from here.
                try:
                    self._refresh_bridge.done.emit()
                except RuntimeError:
                    pass  # widget already destroyed — nothing to update

        self._log("[fetch] manual refresh requested — running in background")
        t = threading.Thread(target=_work, name="optional-feed-refresh", daemon=True)
        self._refresh_thread = t
        t.start()

    def _on_refresh_finished(self) -> None:
        """GUI-thread completion handler for the manual refresh."""
        self._refresh_busy = False
        self._log("[fetch] manual refresh done")
        try:
            self.refresh()
        except Exception as exc:
            print(f"[ui] post-refresh redraw failed: {type(exc).__name__}: {exc}",
                  flush=True)

    def wait_for_background_work(self, msec: int = 15000) -> bool:
        """Block until the manual-refresh thread finishes. True if idle."""
        t = getattr(self, "_refresh_thread", None)
        if t is None or not t.is_alive():
            return True
        t.join(msec / 1000.0)
        return not t.is_alive()
