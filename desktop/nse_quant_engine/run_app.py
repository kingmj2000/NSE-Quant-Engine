"""PySide6 desktop runner — glassmorphic black/crimson theme, one-button pipeline.

Install once:
    pip install PySide6 PySide6-WebEngine pandas numpy yfinance
Run:
    python run_app.py   (or double-click run_app.bat)

v4.8
----
- Header + About show a single "v4.8" pill (no descriptor).
- Report content in the Validation and Trade Plan tabs is rendered through
  `md_to_widgets` into themed glass panels, styled tables, and bulleted
  paragraphs — no more raw markdown / pipe tables in a QTextEdit.
- Compare tab replaced with a real `CompareView` (KPI strip + side-by-side
  table + top-movers panel + scatter fallback).
- Per-element glow: each Card/panel gets a border + shadow matching its
  accent color instead of the previous blanket crimson glow.
- Startup self-check logs the header version and the presence of expected
  dashboard blocks to the Activity drawer.
- Auto-close hardening: window is anchored on self, WebEngine + tab renders
  are wrapped so a single render error never tears down the QApplication.
"""
from __future__ import annotations

APP_VERSION = "4.8"
import sys
import json
import threading
import traceback
import faulthandler
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

try:
    from PySide6.QtCore import Qt, Signal, QObject, QThread, QUrl, QPropertyAnimation, QEasingCurve, qInstallMessageHandler, QtMsgType, QTimer
    from PySide6.QtGui import QFont, QStandardItemModel, QStandardItem, QColor, QPalette, QShortcut, QKeySequence
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QPushButton, QVBoxLayout, QHBoxLayout,
        QTextEdit, QCheckBox, QTabWidget, QTableView, QLabel, QGridLayout,
        QStatusBar, QPlainTextEdit, QSplitter, QFrame, QProgressBar, QScrollArea,
        QSizePolicy, QStackedWidget, QToolButton, QMessageBox,
    )
except ImportError:
    print("PySide6 not installed. Run:  pip install PySide6 PySide6-WebEngine")
    sys.exit(1)

import pandas as pd
import orchestrator
import md_to_widgets


# Optional: try to enable the embedded HTML dashboard (QWebEngineView). If the
# import fails (WebEngine wheel not installed), we transparently fall back to
# the external-browser button. Historical Windows access-violation crashes were
# tied to repeated live reloads, so the embedded tab now loads the local file
# once per run instead of polling.
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore
    from PySide6.QtWebEngineCore import QWebEnginePage      # type: ignore
    HAS_WEBENGINE = True
except Exception:
    QWebEngineView = None  # type: ignore
    QWebEnginePage = None  # type: ignore
    HAS_WEBENGINE = False


# --------------------------- Global crash guards ----------------------------
# Any unhandled exception (Python, Qt, worker thread, WebEngine JS) is routed
# through this bridge to the Activity drawer instead of terminating the app.
class _AppCrashBridge(QObject):
    line = Signal(str)

_crash_bridge: "_AppCrashBridge | None" = None
_early_log: list[str] = []
_fault_log_fh = None

def _write_crash_log(msg: str) -> None:
    """Append to output/last_crash.log so run_app.bat can surface it."""
    try:
        log_dir = Path(__file__).resolve().parent / "output"
        log_dir.mkdir(exist_ok=True)
        with (log_dir / "last_crash.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def _log_crash(msg: str) -> None:
    _write_crash_log(msg)
    if _crash_bridge is not None:
        try:
            _crash_bridge.line.emit(msg)
            return
        except Exception:
            pass
    _early_log.append(msg)
    print(msg, file=sys.stderr)


def _install_global_hooks() -> None:
    def _sys_hook(exc_type, exc, tb):
        _log_crash(f"[unhandled] {exc_type.__name__}: {exc}\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.excepthook = _sys_hook

    def _thread_hook(args):
        _log_crash(f"[thread {args.thread.name}] {args.exc_type.__name__}: {args.exc_value}\n"
                   + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass

    def _qt_msg(mode, ctx, message):
        prefix = {QtMsgType.QtDebugMsg: "qt-debug",
                  QtMsgType.QtInfoMsg: "qt-info",
                  QtMsgType.QtWarningMsg: "qt-warn",
                  QtMsgType.QtCriticalMsg: "qt-critical",
                  QtMsgType.QtFatalMsg: "qt-fatal"}.get(mode, "qt")
        _log_crash(f"[{prefix}] {message}")
    try:
        qInstallMessageHandler(_qt_msg)
    except Exception:
        pass


BASE = Path(__file__).resolve().parent
OUT = BASE / "output"
DATA = BASE / "data"


# ----------------------------- Theme (QSS) ----------------------------------
# Palette
# bg gradient        : #0A0B12 → #10131F → #0E0A18
# panel              : rgba(22,24,34,0.62) with white-7% border
# accent (crimson)   : #D8345F  ·  soft #FF6B8F  ·  deep #8F1837
# accent2 (coral)    : #FF8A5C  ·  soft #FFB193
# teal positive      : #38BDB0
# amber caution      : #F2B13C
# green ok           : #3FB950
# red veto/error     : #E5556A  (used sparingly)
QSS = """
* { font-family: 'Segoe UI', 'Inter', sans-serif; color: #ECEDEE; }
QMainWindow { background: #0A0B12; }
QWidget { background: transparent; }
QWidget#AppRoot, QWidget#MainColumn { background: #0A0B12; }
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget,
QAbstractScrollArea, QAbstractScrollArea::viewport {
    background: transparent;
    border: none;
}
QLabel { background: transparent; }

QFrame#Card {
    background: rgba(22,24,34,0.62);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 16px;
}
/* Per-variant border + glow — each card carries the color of its content
   instead of a blanket red glow. Crimson is reserved for primary CTAs. */
QFrame#Card[accent="indigo"] { border: 1px solid rgba(110,139,255,0.42); }
QFrame#Card[accent="teal"]   { border: 1px solid rgba(56,189,176,0.48); }
QFrame#Card[accent="amber"]  { border: 1px solid rgba(242,177,60,0.48); }
QFrame#Card[accent="red"]    { border: 1px solid rgba(229, 85,106,0.48); }
QFrame#Card[accent="blue"]   { border: 1px solid rgba(88,166,255,0.48); }
QFrame#Card[accent="violet"] { border: 1px solid rgba(163,113,247,0.48); }
QFrame#Card[accent="green"]  { border: 1px solid rgba(63,185,80,0.48); }
QLabel#Pill[tone="teal"]   { background: rgba(56,189,176,0.16);  color: #7FE0C6; }
QLabel#Pill[tone="amber"]  { background: rgba(242,177,60,0.16);  color: #F2B13C; }
QLabel#Pill[tone="green"]  { background: rgba(63,185,80,0.16);   color: #7FE0A6; }
QLabel#Pill[tone="blue"]   { background: rgba(88,166,255,0.16);  color: #9CC6FF; }
QLabel#Pill[tone="violet"] { background: rgba(163,113,247,0.16); color: #C6A8FA; }
QLabel#Pill[tone="red"]    { background: rgba(229,85,106,0.18);  color: #FF8597; }
QLabel#Pill[tone="dim"]    { background: rgba(255,255,255,0.05); color: #B7BCC6; }

QFrame#Drawer {
    background: rgba(14,16,26,0.92);
    border-left: 1px solid rgba(255,255,255,0.08);
}
QFrame#TopBar {
    background: rgba(16,18,28,0.55);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 18px;
}

QLabel#Header    { font-size: 19px; font-weight: 700; letter-spacing: 0.3px; }
QLabel#Sub       { color: #8A92A6; font-size: 12px; }
QLabel#Metric    { font-size: 26px; font-weight: 800; color: #FFFFFF; }
QLabel#MetricInd { font-size: 26px; font-weight: 800; color: #FF6B8F; }
QLabel#Pill {
    padding: 4px 11px; border-radius: 10px;
    background: rgba(216,52,95,0.15);
    color: #FF6B8F; font-weight: 600; font-size: 11px;
}
QLabel#PillAge {
    padding: 4px 11px; border-radius: 10px;
    background: rgba(255,255,255,0.05);
    color: #B7BCC6; font-weight: 600; font-size: 11px;
}

QPushButton#Primary {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #D8345F, stop:1 #FF8A5C);
    color: white; font-weight: 700; font-size: 14px;
    border: none; border-radius: 14px;
    padding: 11px 22px;
}
QPushButton#Primary:hover  {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
        stop:0 #E54870, stop:1 #FF9B73);
}
QPushButton#Primary:disabled{ background: #23252F; color: #6B6B72; }

QPushButton#Ghost {
    background: rgba(255,255,255,0.04);
    color: #DEE0E5; font-weight: 600; font-size: 13px;
    border: 1px solid rgba(255,255,255,0.10);
    border-radius: 12px; padding: 9px 16px;
}
QPushButton#Ghost:hover  { background: rgba(216,52,95,0.14); border-color: rgba(216,52,95,0.40); color: #fff; }

QToolButton#Drawer {
    background: rgba(216,52,95,0.12);
    color: #FF6B8F; font-weight: 700;
    border: 1px solid rgba(216,52,95,0.30);
    border-radius: 12px; padding: 8px 12px;
}
QToolButton#Drawer:hover { background: rgba(216,52,95,0.22); color: #fff; }

QCheckBox { spacing: 8px; color: #C8C9CC; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid #3A3A45; background: #14141A; }
QCheckBox::indicator:checked { background: #D8345F; border-color: #D8345F; }

QTabWidget::pane { border: none; background: transparent; top: 8px; }
QTabBar::tab {
    background: transparent; color: #8C8F94;
    padding: 8px 18px; margin-right: 4px;
    border-radius: 10px; font-weight: 600;
}
QTabBar::tab:selected { background: rgba(216,52,95,0.18); color: #FFFFFF; }
QTabBar::tab:hover:!selected { color: #ECEDEE; }

QPlainTextEdit, QTextEdit, QTextBrowser {
    background: transparent;
    border: 1px solid rgba(255,255,255,0.05);
    border-radius: 12px; padding: 10px;
    color: #D6D7DA;
    selection-background-color: rgba(216,52,95,0.30);
}
QTableView {
    background: transparent;
    background-color: transparent;
    gridline-color: rgba(255,255,255,0.04);
    border: none;
    border-radius: 10px;
    selection-background-color: rgba(216,52,95,0.28);
    selection-color: #fff;
    alternate-background-color: transparent;
    outline: 0;
}
QTableView::item { background: transparent; border: none; padding: 6px 8px; }
QTableView::item:alternate { background: transparent; }
QHeaderView::section {
    background: rgba(255,255,255,0.025); color: #8A92A6;
    padding: 6px 8px; border: none;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    font-weight: 600;
}

QProgressBar {
    background: #14141A; border: none; border-radius: 6px; height: 10px; text-align: center;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #D8345F, stop:1 #FF8A5C);
    border-radius: 6px;
}
QStatusBar { background: transparent; color: #8C8F94; }
QScrollBar:vertical { background: transparent; width: 10px; }
QScrollBar::handle:vertical { background: #2A2A33; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #D8345F; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: #2A2A33; border-radius: 5px; min-width: 30px; }
"""


# ----------------------------- Helpers --------------------------------------
class LogBridge(QObject):
    line = Signal(str)
    step = Signal(dict)


class RunnerThread(QThread):
    done = Signal(dict)

    def __init__(self, steps, bridge: LogBridge, include_shadow: bool = True, include_fetch: bool = True):
        super().__init__()
        self.steps = steps
        self.bridge = bridge
        self.include_shadow = include_shadow
        self.include_fetch = include_fetch

    def run(self):
        summary = None
        try:
            cmd = [sys.executable, str(BASE / "orchestrator.py"), "--all"]
            if not self.include_shadow:
                cmd.append("--no-shadow")
            if not self.include_fetch:
                cmd.append("--skip-fetch")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            self.bridge.line.emit("Launching isolated pipeline process: " + " ".join(cmd))
            proc = subprocess.Popen(
                cmd,
                cwd=str(BASE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
            current_step = ""
            step_re = re.compile(r"^→\s+(.+?)\s*$")
            done_re = re.compile(r"done in\s+([0-9.]+)s\s+\[(\w+)\]")
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = raw.rstrip("\r\n")
                    print(line, flush=True)
                    self.bridge.line.emit(line)
                    m_step = step_re.match(line.strip())
                    if m_step:
                        current_step = m_step.group(1)
                        continue
                    m_done = done_re.search(line)
                    if m_done and current_step:
                        self.bridge.step.emit({
                            "name": current_step,
                            "status": m_done.group(2),
                            "duration_s": float(m_done.group(1)),
                            "error": "",
                        })
            rc = proc.wait()
            manifest_path = OUT / "run_manifest.json"
            if manifest_path.exists():
                try:
                    summary = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if "finished" not in summary and summary.get("completed_at"):
                        summary["finished"] = summary.get("completed_at")
                    if "duration_s" not in summary:
                        summary["duration_s"] = 0
                except Exception:
                    summary = None
            if rc != 0:
                crash_code = rc in (-1073741819, 3221225477)
                err = "native access violation in isolated pipeline process" if crash_code else f"pipeline process exited with code {rc}"
                self.bridge.line.emit(f"CHILD PIPELINE FAILED — {err}. Desktop app kept open.")
                _write_crash_log(f"[child-process] {err}")
                if summary is None:
                    summary = {
                        "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "duration_s": 0,
                        "steps": [{"name": "isolated pipeline", "status": "error", "duration_s": 0, "error": err}],
                    }
                else:
                    summary.setdefault("steps", []).append({"name": "isolated pipeline", "status": "error", "duration_s": 0, "error": err})
        except BaseException as exc:
            tb = traceback.format_exc()
            self.bridge.line.emit(f"FATAL runner error kept app open: {type(exc).__name__}: {exc}")
            self.bridge.line.emit(tb)
            summary = {
                "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "duration_s": 0,
                "steps": [{"name": "runner", "status": "error", "duration_s": 0, "error": f"{type(exc).__name__}: {exc}"}],
            }
        if summary is None:
            summary = {
                "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "finished": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "duration_s": 0,
                "steps": [{"name": "runner", "status": "error", "duration_s": 0, "error": "no summary returned"}],
            }
        self.done.emit(summary)


def _df_to_model(df: pd.DataFrame) -> QStandardItemModel:
    model = QStandardItemModel(len(df), len(df.columns))
    model.setHorizontalHeaderLabels([str(c) for c in df.columns])
    for r, row in enumerate(df.itertuples(index=False)):
        for c, v in enumerate(row):
            it = QStandardItem("" if pd.isna(v) else str(v))
            it.setEditable(False)
            model.setItem(r, c, it)
    return model


def _human_age(iso_ts: str | None) -> str:
    if not iso_ts:
        return "no runs yet"
    try:
        ts = datetime.strptime(iso_ts, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso_ts
    delta = datetime.now() - ts
    s = int(delta.total_seconds())
    if s < 60:    return f"{s}s ago"
    if s < 3600:  return f"{s//60}m ago"
    if s < 86400: return f"{s//3600}h ago"
    return f"{s//86400}d ago"


if HAS_WEBENGINE and QWebEnginePage is not None:
    class DashboardPage(QWebEnginePage):
        def __init__(self, callback=None, parent=None):
            super().__init__(parent)
            self.callback = callback

        def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
            try:
                if self.callback and message:
                    self.callback(f"[dashboard js] {message} (line {lineNumber})")
            except Exception:
                pass
            # never re-raise: a JS error must not close the window
            try:
                return super().javaScriptConsoleMessage(level, message, lineNumber, sourceID)
            except Exception:
                return None
else:
    DashboardPage = None


# ----------------------------- Dashboard ------------------------------------
class Dashboard(QWidget):
    """Stable native summary dashboard.

    The full Chart.js dashboard is still generated to output/dashboard_latest.html
    and opened through the browser button. The in-app dashboard intentionally
    stays native Qt because repeated local-file WebEngine refreshes after long
    runs were causing Windows access-violation exits (-1073741819).
    """
    def __init__(self):
        super().__init__()
        self._console_callback = None
        self.view = None
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(12)

        intro = QFrame(); intro.setObjectName("Card"); intro.setProperty("accent", "blue")
        iv = QVBoxLayout(intro); iv.setContentsMargins(18, 14, 18, 14); iv.setSpacing(6)
        title = QLabel("Run Summary")
        title.setStyleSheet("font-size:18px;font-weight:750;color:#FFFFFF;background:transparent;")
        sub = QLabel("Stable native overview. Use Open in browser for the full interactive HTML dashboard.")
        sub.setObjectName("Sub"); sub.setWordWrap(True)
        iv.addWidget(title); iv.addWidget(sub)
        root.addWidget(intro)

        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(10); self.grid.setVerticalSpacing(10)
        root.addLayout(self.grid)

        self.note = QLabel("No run on disk yet — click Run Full Pipeline.")
        self.note.setObjectName("Sub"); self.note.setWordWrap(True)
        root.addWidget(self.note)
        root.addStretch()

    def set_console_callback(self, callback):
        self._console_callback = callback

    def _emit_console(self, msg: str):
        if self._console_callback:
            self._console_callback(msg)

    def _html_path(self) -> Path:
        return OUT / "dashboard_latest.html"

    def open_browser(self):
        p = self._html_path()
        if p.exists():
            import webbrowser; webbrowser.open(p.as_uri())

    def refresh(self):
        for i in reversed(range(self.grid.count())):
            item = self.grid.takeAt(i)
            if item.widget():
                item.widget().deleteLater()

        def load_csv(p: Path) -> pd.DataFrame:
            try:
                return pd.read_csv(p) if p.exists() else pd.DataFrame()
            except Exception:
                return pd.DataFrame()

        def load_json(p: Path) -> dict:
            try:
                return json.loads(p.read_text(encoding="utf-8").replace(": NaN", ": null")) if p.exists() else {}
            except Exception:
                return {}

        status = load_json(OUT / "validation_status.json")
        manifest = load_json(OUT / "run_manifest.json")
        scores = load_csv(OUT / "latest_scores.csv")
        trade = load_csv(OUT / "trade_plan_latest.csv")
        shadow = load_csv(OUT / "latest_scores_v4_shadow.csv")
        fwd = load_csv(OUT / "forward_return_history.csv")

        matured = maturing = total = 0
        if not fwd.empty:
            f = fwd
            if "Horizon_Days" in f.columns:
                try:
                    f10 = f[pd.to_numeric(f["Horizon_Days"], errors="coerce") == 10]
                    if not f10.empty:
                        f = f10
                except Exception:
                    pass
            total = int(len(f))
            if "Net_Forward_Return" in f.columns:
                matured = int(f["Net_Forward_Return"].notna().sum())
                maturing = int(f["Net_Forward_Return"].isna().sum())
            else:
                maturing = total
        rate = f"{(matured / total * 100):.1f}%" if total else "—"
        verdict = str(status.get("verdict") or "—")
        cards = [
            ("Latest scores", str(len(scores)) if not scores.empty else "—", "blue", "official scoring rows"),
            ("Trade plan", str(len(trade)) if not trade.empty else "—", "teal", "watchlist / plan rows"),
            ("Shadow rows", str(len(shadow)) if not shadow.empty else "—", "violet", "shadow scoring rows"),
            ("Verdict", verdict, "green" if verdict == "Validation Positive" else "amber", str(status.get("evidence_grade") or "")),
            ("Matured", str(matured), "teal", "10-day forward rows"),
            ("Awaiting maturation", str(maturing), "amber", "10-day forward rows"),
            ("Total signals", str(total) if total else "—", "blue", "10-day slice"),
            ("Maturation rate", rate, "green" if matured else "violet", "matured / total"),
        ]
        for i, (title, value, tone, subtitle) in enumerate(cards):
            self.grid.addWidget(_make_kpi_card(title, value, tone, subtitle), i // 4, i % 4)

        # Activation checklist for optional inputs (drives Fincept/Vibe overlays).
        # Files are auto-fetched at the start of every pipeline run by
        # core.optional_data_fetchers.refresh_all(); user drops still win when newer.
        checklist = [
            ("data/fii_dii_daily.csv",       "FII/DII flow (Step 14, Fincept · auto-fetched)"),
            ("data/bulk_deals.csv",          "Bulk deals (Step 14, Fincept · auto-fetched)"),
            ("data/fundamentals_latest.csv", "Fundamentals overlay (Step 6 · auto-fetched via yfinance)"),
            ("data/earnings_calendar.csv",   "Earnings calendar (Step 11, Fincept · auto-fetched)"),
        ]
        chk_head = QLabel("Overlay activation · auto-refreshed each run · drop your own CSV in data/ to override")
        chk_head.setObjectName("Sub"); chk_head.setWordWrap(True)
        chk_head.setStyleSheet("font-size:11px;letter-spacing:1px;text-transform:uppercase;margin-top:10px;")
        self.grid.addWidget(chk_head, (len(cards) + 3) // 4, 0, 1, 4)
        base_row = (len(cards) + 3) // 4 + 1

        def _fmt_age(mtime: float) -> str:
            s = int(time.time() - mtime)
            if s < 60:    return f"{s}s ago"
            if s < 3600:  return f"{s//60}m ago"
            if s < 86400: return f"{s//3600}h ago"
            return f"{s//86400}d ago"

        for i, (rel, label) in enumerate(checklist):
            p = BASE / rel
            if p.exists():
                try:
                    rows = sum(1 for _ in open(p, "r", encoding="utf-8", errors="ignore")) - 1
                    rows = max(rows, 0)
                except Exception:
                    rows = 0
                age_h = (time.time() - p.stat().st_mtime) / 3600.0
                if age_h < 24:
                    mark, tone = "✅", "green"
                elif age_h < 24 * 7:
                    mark, tone = "🟡", "amber"
                else:
                    mark, tone = "🟠", "amber"
                sub = f"{rows} rows · updated {_fmt_age(p.stat().st_mtime)}"
            else:
                mark, tone = "⚠️", "amber"
                sub = "missing — auto-fetch will try next run; overlay quiet meanwhile"
            self.grid.addWidget(_make_kpi_card(f"{mark}  {label}", rel, tone, sub),
                                base_row + i // 4, i % 4)

        # Refresh-now button lets the user re-pull the 4 feeds without a full run.
        refresh_row = base_row + (len(checklist) + 3) // 4
        btn = QPushButton("🔄 Refresh optional feeds now")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet("padding:8px 14px;")
        btn.clicked.connect(self._refresh_optional_feeds)
        self.grid.addWidget(btn, refresh_row, 0, 1, 2)

        html = self._html_path()
        complete = manifest.get("completed_at") or "latest artifacts"
        self.note.setText(
            f"Last completed: {complete}. Interactive dashboard file: {html if html.exists() else 'not generated yet'}. "
            f"See INSPIRATION_MAP.md for what Fincept Terminal / Vibe Trading concepts each overlay borrows."
        )

    def _refresh_optional_feeds(self):
        """Fire the 4 optional-CSV fetchers in a background thread and reload the dashboard."""
        self._emit_console("[fetch] manual refresh requested — running in background")
        from PySide6.QtCore import QThread, Signal
        parent = self

        class _RefreshThread(QThread):
            done_signal = Signal()
            def run(self_inner):
                try:
                    if str(BASE) not in sys.path:
                        sys.path.insert(0, str(BASE))
                    from core.optional_data_fetchers import refresh_all
                    refresh_all(BASE)
                except Exception as e:
                    print(f"[fetch] manual refresh failed: {e}", flush=True)
                self_inner.done_signal.emit()

        t = _RefreshThread(self)
        t.done_signal.connect(lambda: (parent._emit_console("[fetch] manual refresh done"),
                                       parent.load_last_run()))
        # Keep a reference so Qt doesn't garbage-collect the thread mid-run.
        self._refresh_thread = t
        t.start()


# ----------------------------- Run Drawer -----------------------------------
class RunDrawer(QFrame):
    """Collapsible right-side panel holding the per-step progress + scrolling log.

    Closed by default — the dashboard owns the full window. Opens when the user
    starts a run (auto) or clicks ☰ / presses F9.
    """
    EXPANDED_W = 380
    COLLAPSED_W = 0

    def __init__(self):
        super().__init__()
        self.setObjectName("Drawer")
        self.setFixedWidth(self.COLLAPSED_W)
        self._anim = QPropertyAnimation(self, b"minimumWidth")
        self._anim.setDuration(220); self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim2 = QPropertyAnimation(self, b"maximumWidth")
        self._anim2.setDuration(220); self._anim2.setEasingCurve(QEasingCurve.OutCubic)

        wrap = QVBoxLayout(self); wrap.setContentsMargins(16, 16, 16, 16); wrap.setSpacing(12)

        title_row = QHBoxLayout()
        t = QLabel("Run activity"); t.setObjectName("Header")
        title_row.addWidget(t); title_row.addStretch()
        self.lbl_status = QLabel("idle"); self.lbl_status.setObjectName("Sub")
        title_row.addWidget(self.lbl_status)
        wrap.addLayout(title_row)

        # Step list
        steps_label = QLabel("Pipeline steps"); steps_label.setObjectName("Sub")
        wrap.addWidget(steps_label)
        self.steps_area = QScrollArea(); self.steps_area.setWidgetResizable(True)
        self.steps_area.setFrameShape(QFrame.NoFrame)
        self.steps_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.steps_area.setMaximumHeight(280)
        self.steps_inner = QWidget()
        self.steps_v = QVBoxLayout(self.steps_inner)
        self.steps_v.setContentsMargins(0, 0, 0, 0); self.steps_v.setSpacing(5)
        self.steps_v.addStretch()
        self.steps_area.setWidget(self.steps_inner)
        wrap.addWidget(self.steps_area)
        self.step_cells: dict[str, QLabel] = {}

        log_label = QLabel("Log"); log_label.setObjectName("Sub")
        wrap.addWidget(log_label)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        wrap.addWidget(self.log, 1)

    # ---- programmatic API ----
    def set_open(self, opened: bool):
        target = self.EXPANDED_W if opened else self.COLLAPSED_W
        self._anim.stop(); self._anim2.stop()
        self._anim.setStartValue(self.width()); self._anim.setEndValue(target)
        self._anim2.setStartValue(self.width()); self._anim2.setEndValue(target)
        self._anim.start(); self._anim2.start()

    def is_open(self) -> bool:
        return self.width() > 20

    def toggle(self):
        self.set_open(not self.is_open())

    def append_log(self, msg: str):
        self.log.appendPlainText(msg)
        sb = self.log.verticalScrollBar(); sb.setValue(sb.maximum())

    def reset_steps(self, names: list[str]):
        # clear
        while self.steps_v.count():
            it = self.steps_v.takeAt(0)
            w = it.widget()
            if w: w.deleteLater()
        self.step_cells.clear()
        for n in names:
            lbl = QLabel(f"○  {n}")
            lbl.setStyleSheet(self._cell_style("pending"))
            self.steps_v.addWidget(lbl); self.step_cells[n] = lbl
        self.steps_v.addStretch()

    def update_step(self, name: str, status: str, duration_s: float):
        lbl = self.step_cells.get(name)
        if not lbl: return
        icon = {"ok": "●", "error": "✕", "skipped": "◌"}.get(status, "○")
        lbl.setText(f"{icon}  {name}    {duration_s:.1f}s")
        lbl.setStyleSheet(self._cell_style(status))

    def set_status(self, text: str):
        self.lbl_status.setText(text)

    @staticmethod
    def _cell_style(status: str) -> str:
        colors = {
            "pending": ("#8A92A6", "rgba(255,255,255,0.03)"),
            "ok":      ("#7FE0C6", "rgba(56,189,176,0.14)"),
            "error":   ("#FF8597", "rgba(229,85,106,0.18)"),
            "skipped": ("#E6BB6A", "rgba(242,177,60,0.14)"),
        }
        fg, bg = colors.get(status, colors["pending"])
        return (f"padding:7px 10px;border-radius:8px;background:{bg};"
                f"color:{fg};font-weight:600;font-size:12px;")


# --------------------------- Rich tab renderers -----------------------------
# Structured cards + tables replace raw CSV/MD dumps for the DQ, Trade Plan
# and Validation tabs.
_PILL_TONE_FOR_STATUS = {"ok": "teal", "warn": "amber", "bad": "red", "info": "blue", "muted": "dim"}


def _make_pill(text: str, tone: str = "dim") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Pill")
    lbl.setProperty("tone", tone)
    lbl.style().unpolish(lbl); lbl.style().polish(lbl)
    return lbl


def _make_kpi_card(title: str, value: str, tone: str = "dim", subtitle: str = "") -> QFrame:
    card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", tone)
    v = QVBoxLayout(card); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(4)
    t = QLabel(title.upper()); t.setObjectName("Sub"); t.setStyleSheet("letter-spacing:1px;font-size:10.5px;")
    val = QLabel(value); val.setObjectName("Metric")
    tone_color = {"teal": "#7FE0C6", "amber": "#F2B13C", "red": "#FF8597",
                  "blue": "#9CC6FF", "violet": "#C6A8FA", "green": "#7FE0A6",
                  "indigo": "#A9BCFF", "dim": "#FFFFFF"}.get(tone, "#FFFFFF")
    val.setStyleSheet(f"color:{tone_color};font-size:26px;font-weight:800;")
    v.addWidget(t); v.addWidget(val)
    if subtitle:
        s = QLabel(subtitle); s.setObjectName("Sub"); s.setWordWrap(True)
        v.addWidget(s)
    return card


class DQReportView(QWidget):
    """Structured Data-Quality dashboard: KPI header + colored flag table."""
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(10)
        self.kpi_row = QGridLayout(); self.kpi_row.setHorizontalSpacing(10); self.kpi_row.setVerticalSpacing(10)
        outer.addLayout(self.kpi_row)
        self.body = QScrollArea(); self.body.setWidgetResizable(True); self.body.setFrameShape(QFrame.NoFrame)
        self._body_holder = QWidget(); self._body_v = QVBoxLayout(self._body_holder)
        self._body_v.setContentsMargins(0, 0, 0, 0); self._body_v.setSpacing(10)
        self.body.setWidget(self._body_holder)
        outer.addWidget(self.body, 1)

    def _clear(self):
        while self.kpi_row.count():
            it = self.kpi_row.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        while self._body_v.count():
            it = self._body_v.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def render(self, dq_summary: dict, quality_df: pd.DataFrame):
        self._clear()
        rows = dq_summary.get("rows") or (len(quality_df) if quality_df is not None else 0)
        health = dq_summary.get("health_score")
        cov_act = dq_summary.get("coverage_actionable", {}) or {}
        cov_str = dq_summary.get("coverage_structural", {}) or {}
        def _pct(v):
            try: return f"{float(v)*100:.0f}%"
            except Exception: return "—"
        kpis = [
            ("Rows analysed", str(rows), "blue", "ETF universe covered"),
            ("Health score", f"{health if health is not None else '—'} / 100", "teal", "0 = broken · 100 = pristine"),
            ("NAV filled", _pct(cov_act.get("nav")), "green", "actionable"),
            ("AUM filled", _pct(cov_act.get("aum")), "green", "actionable"),
            ("TER filled", _pct(cov_act.get("ter")), "amber", "actionable"),
            ("Benchmark", _pct(cov_act.get("benchmark")), "violet", "actionable"),
            ("Tracking quality", _pct(cov_str.get("tracking") or cov_act.get("tracking")), "blue", "TE or tracking-difference"),
        ]
        for i, (title, val, tone, sub) in enumerate(kpis):
            self.kpi_row.addWidget(_make_kpi_card(title, val, tone, sub), i // 4, i % 4)

        flags = dq_summary.get("flag_counts", {}) or {}
        if flags:
            head = QLabel("ETF quality flag distribution"); head.setObjectName("Sub")
            head.setStyleSheet("font-size:11px;letter-spacing:1px;text-transform:uppercase;")
            self._body_v.addWidget(head)
            grid = QGridLayout(); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(8)
            for i, (flag, count) in enumerate(sorted(flags.items(), key=lambda x: -x[1])):
                tone = "teal" if flag.lower() == "complete" else ("amber" if "missing" in flag.lower() else "amber")
                card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", tone)
                cv = QHBoxLayout(card); cv.setContentsMargins(12, 8, 12, 8)
                cv.addWidget(_make_pill(str(count), tone))
                lbl = QLabel(flag); lbl.setWordWrap(True); lbl.setStyleSheet("color:#DEE0E5;font-size:12.5px;")
                cv.addWidget(lbl, 1)
                grid.addWidget(card, i // 2, i % 2)
            holder = QWidget(); holder.setLayout(grid)
            self._body_v.addWidget(holder)

        if quality_df is not None and not quality_df.empty:
            head = QLabel("Per-ETF quality snapshot"); head.setObjectName("Sub")
            head.setStyleSheet("font-size:11px;letter-spacing:1px;text-transform:uppercase;margin-top:8px;")
            self._body_v.addWidget(head)
            preview_cols = [c for c in ("Symbol", "Name", "ETF_Quality_Data_Flag",
                                        "NAV", "AUM_INR_Cr", "TER", "Benchmark_Index",
                                        "Tracking_Error", "Tracking_Difference")
                            if c in quality_df.columns]
            tbl = QTableView(); tbl.setModel(_df_to_model(quality_df[preview_cols].head(400)))
            tbl.horizontalHeader().setStretchLastSection(True); tbl.verticalHeader().setVisible(False)
            tbl.setAlternatingRowColors(False)
            tbl.setMinimumHeight(420)
            self._body_v.addWidget(tbl, 1)
        self._body_v.addStretch()


class TradePlanView(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(10)
        self.header_row = QHBoxLayout(); self.header_row.setSpacing(10)
        outer.addLayout(self.header_row)
        self.body = QScrollArea(); self.body.setWidgetResizable(True); self.body.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget(); self._v = QVBoxLayout(self._holder)
        self._v.setContentsMargins(0, 0, 0, 0); self._v.setSpacing(10)
        self.body.setWidget(self._holder)
        outer.addWidget(self.body, 1)

    def _clear(self):
        while self.header_row.count():
            it = self.header_row.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        while self._v.count():
            it = self._v.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def render(self, trade_df: pd.DataFrame, validation_status: dict, report_text: str):
        self._clear()
        verdict = validation_status.get("verdict", "Insufficient History")
        grade = validation_status.get("evidence_grade", "Insufficient Evidence")
        use_mode = "LIVE" if verdict == "Validation Positive" else "WATCHLIST ONLY"
        tone_v = "green" if verdict == "Validation Positive" else "amber"
        tone_m = "green" if use_mode == "LIVE" else "amber"
        self.header_row.addWidget(_make_kpi_card("Verdict", verdict, tone_v))
        self.header_row.addWidget(_make_kpi_card("Evidence grade", grade, "blue"))
        self.header_row.addWidget(_make_kpi_card("Use mode", use_mode, tone_m,
                                                 "Trade levels are mechanical reference levels only." if use_mode != "LIVE" else ""))
        self.header_row.addWidget(_make_kpi_card("Plan rows", str(len(trade_df) if trade_df is not None else 0), "violet"))
        self.header_row.addStretch()

        if trade_df is None or trade_df.empty:
            note = QLabel("(no trade plan yet — run the pipeline)")
            note.setObjectName("Sub"); self._v.addWidget(note); self._v.addStretch(); return

        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(10)
        preview = trade_df.head(24) if "Final_Score" not in trade_df.columns else \
                  trade_df.sort_values("Final_Score", ascending=False).head(24)
        for i, (_, r) in enumerate(preview.iterrows()):
            grid.addWidget(self._card_for_row(r), i // 3, i % 3)
        holder = QWidget(); holder.setLayout(grid)
        self._v.addWidget(holder)

        if report_text:
            head = QLabel("Report notes"); head.setObjectName("Sub")
            head.setStyleSheet("font-size:11px;letter-spacing:1px;text-transform:uppercase;margin-top:8px;")
            self._v.addWidget(head)
            try:
                md_to_widgets.render_markdown(report_text, self._v)
            except Exception as e:
                _log_crash(f"Trade-plan report render failed: {e}")
        self._v.addStretch()

    def _card_for_row(self, r: pd.Series) -> QFrame:
        def _num(v, nd=2):
            try:
                f = float(v)
                if pd.isna(f): return "—"
                return f"{f:,.{nd}f}"
            except Exception:
                return "—"
        risk = str(r.get("Key_Risk", "") or "").strip().lower()
        if "veto" in risk: tone = "amber"
        elif "overbought" in risk or "elevated" in risk: tone = "amber"
        else: tone = "teal"

        card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", tone)
        v = QVBoxLayout(card); v.setContentsMargins(14, 12, 14, 12); v.setSpacing(6)
        top = QHBoxLayout()
        sym = QLabel(str(r.get("Symbol", "?"))); sym.setStyleSheet("font-size:15px;font-weight:700;color:#fff;")
        bucket = str(r.get("Bucket", "") or "")
        bt = "teal" if "Top" in bucket else "amber" if "Risky" in bucket else "blue"
        pill = _make_pill(bucket or "—", bt)
        top.addWidget(sym); top.addStretch(); top.addWidget(pill)
        v.addLayout(top)
        name = QLabel(str(r.get("Name", "") or "")); name.setStyleSheet("color:#8A92A6;font-size:11px;"); name.setWordWrap(True)
        v.addWidget(name)

        grid = QGridLayout(); grid.setHorizontalSpacing(8); grid.setVerticalSpacing(4)
        def _cell(row, col, label, val, color="#ECEDEE"):
            l = QLabel(label); l.setStyleSheet("background:transparent;color:#6B6F76;font-size:10px;text-transform:uppercase;letter-spacing:.4px;")
            n = QLabel(val); n.setStyleSheet(f"background:transparent;color:{color};font-size:12.5px;font-weight:650;")
            grid.addWidget(l, row * 2, col); grid.addWidget(n, row * 2 + 1, col)
        _cell(0, 0, "Buy zone", f"{_num(r.get('Buy_Zone_Low'))}–{_num(r.get('Buy_Zone_High'))}")
        _cell(0, 1, "Stop", _num(r.get('Stop_Loss')), "#F2B13C")
        _cell(0, 2, "Hold", f"{int(r.get('Hold_Days_Min',5) or 5)}–{int(r.get('Hold_Days_Max',15) or 15)}d")
        _cell(1, 0, "Target 1", _num(r.get('Target_1')), "#7FE0C6")
        _cell(1, 1, "Target 2", _num(r.get('Target_2')), "#7FE0C6")
        _cell(1, 2, "Score", _num(r.get('Final_Score'), 1), "#9CC6FF")
        v.addLayout(grid)

        reason = str(r.get("Reason", "") or "").strip()
        if reason:
            rl = QLabel(reason); rl.setWordWrap(True); rl.setStyleSheet("color:#B7BCC6;font-size:11.5px;margin-top:4px;")
            v.addWidget(rl)
        if risk and risk != "no major technical risk flagged":
            rk = QLabel(f"Risk: {r.get('Key_Risk')}"); rk.setWordWrap(True)
            rk.setStyleSheet("color:#F2B13C;font-size:11.5px;font-weight:600;")
            v.addWidget(rk)
        return card


class ValidationView(QWidget):
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(10)
        self.top = QGridLayout(); self.top.setHorizontalSpacing(10); self.top.setVerticalSpacing(10)
        outer.addLayout(self.top)
        self.body = QScrollArea(); self.body.setWidgetResizable(True); self.body.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget(); self._v = QVBoxLayout(self._holder)
        self._v.setContentsMargins(0, 0, 0, 0); self._v.setSpacing(10)
        self.body.setWidget(self._holder)
        outer.addWidget(self.body, 1)

    def _clear(self):
        while self.top.count():
            it = self.top.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        while self._v.count():
            it = self._v.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def render(self, status: dict, report_text: str):
        self._clear()
        verdict = status.get("verdict", "Insufficient History")
        grade = status.get("evidence_grade", "Insufficient Evidence")
        horizon = status.get("horizon_days", "—")
        stats = status.get("stats", {}) or {}
        tone_v = "green" if verdict == "Validation Positive" else "amber"

        def fmt(v, nd=2, suf=""):
            try:
                f = float(v)
                if pd.isna(f): return "—"
                return f"{f:.{nd}f}{suf}"
            except Exception:
                return "—"
        cards = [
            ("Verdict", verdict, tone_v, f"Horizon: {horizon}d"),
            ("Evidence grade", grade, "blue", ""),
            ("Validation dates", fmt(stats.get("validation_dates"), 0), "violet", "distinct signal dates"),
            ("Effective dates", fmt(stats.get("effective_validation_dates"), 1), "violet", "quality-weighted"),
            ("Top-Bottom spread", fmt(stats.get("spread"), 4), "teal", "quintile spread (return units)"),
            ("Hit rate", fmt((stats.get("hit_rate") or 0) * 100, 1, "%"), "teal", "top-beats-bottom days"),
            ("Adj. t-stat", fmt(stats.get("adj_tstat"), 2), "amber", ">=2 = strong"),
            ("Bootstrap P(+)", fmt(stats.get("bootstrap_prob"), 2), "amber", ">=0.9 = strong"),
        ]
        for i, (t, v, tone, sub) in enumerate(cards):
            self.top.addWidget(_make_kpi_card(t, v, tone, sub), i // 4, i % 4)

        if report_text:
            head = QLabel("Validation report"); head.setObjectName("Sub")
            head.setStyleSheet("font-size:11px;letter-spacing:1px;text-transform:uppercase;")
            self._v.addWidget(head)
            try:
                md_to_widgets.render_markdown(report_text, self._v)
            except Exception as e:
                _log_crash(f"Validation report render failed: {e}")
        self._v.addStretch()


# --------------------------- Compare view -----------------------------------
class CompareView(QWidget):
    """Shadow-vs-Official comparison: KPI strip + side-by-side table + movers."""
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(10)
        self.kpi = QGridLayout(); self.kpi.setHorizontalSpacing(10); self.kpi.setVerticalSpacing(10)
        outer.addLayout(self.kpi)
        self.body = QScrollArea(); self.body.setWidgetResizable(True); self.body.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget(); self._v = QVBoxLayout(self._holder)
        self._v.setContentsMargins(0, 0, 0, 0); self._v.setSpacing(10)
        self.body.setWidget(self._holder)
        outer.addWidget(self.body, 1)

    def _clear(self):
        while self.kpi.count():
            it = self.kpi.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        while self._v.count():
            it = self._v.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def render(self, cmp_df: pd.DataFrame, cmp_json: dict):
        self._clear()
        # KPI strip driven by shadow_vs_official.json when present
        def _fmt(v, nd=2, suf=""):
            try:
                f = float(v)
                if pd.isna(f): return "—"
                return f"{f:.{nd}f}{suf}"
            except Exception:
                return "—"
        jacc = cmp_json.get("jaccard_at_20") or cmp_json.get("jaccard")
        spear = cmp_json.get("spearman") or cmp_json.get("spearman_full")
        avg_dr = cmp_json.get("avg_abs_delta_rank") or cmp_json.get("mean_abs_delta_rank")
        vagree = cmp_json.get("verdict_agreement") or cmp_json.get("recommendation") or "—"
        kpis = [
            ("Jaccard@20", _fmt(jacc, 2), "teal", "Top-20 overlap"),
            ("Spearman", _fmt(spear, 2), "blue", "Full ranking correlation"),
            ("Avg |ΔRank|", _fmt(avg_dr, 1), "amber", "Lower = more agreement"),
            ("Verdict", str(vagree)[:60] or "—", "violet", "Recommendation"),
        ]
        for i, (t, v, tone, sub) in enumerate(kpis):
            self.kpi.addWidget(_make_kpi_card(t, v, tone, sub), 0, i)

        if cmp_df is None or cmp_df.empty or len(cmp_df) < 2:
            note = QFrame(); note.setObjectName("Card"); note.setProperty("accent", "amber")
            nv = QVBoxLayout(note); nv.setContentsMargins(16, 14, 16, 14)
            title = QLabel("Shadow run neutralized — insufficient shadow evidence")
            title.setStyleSheet("color:#F2B13C;font-weight:700;font-size:14px;")
            hint = QLabel("The shadow pipeline did not produce enough matured signals for a "
                          "meaningful side-by-side comparison. This is expected during "
                          "accumulation; check back after more forward-return windows close.")
            hint.setWordWrap(True); hint.setStyleSheet("color:#B7BCC6;font-size:12px;margin-top:4px;")
            nv.addWidget(title); nv.addWidget(hint)
            self._v.addWidget(note); self._v.addStretch(); return

        # Build side-by-side table
        df = cmp_df.copy()
        # find likely column names, tolerate variations
        def _pick(*names):
            for n in names:
                if n in df.columns: return n
            return None
        c_sym = _pick("Symbol", "symbol")
        c_bO = _pick("Bucket_Official", "Official_Bucket", "Bucket")
        c_bS = _pick("Bucket_Shadow", "Shadow_Bucket")
        c_rO = _pick("Rank_Official", "Official_Rank")
        c_rS = _pick("Rank_Shadow", "Shadow_Rank")
        c_sO = _pick("Score_Official", "Official_Score", "Final_Score_Official")
        c_sS = _pick("Score_Shadow", "Shadow_Score", "Final_Score_Shadow")

        display_cols = [x for x in [c_sym, c_bO, c_bS, c_rO, c_rS, c_sO, c_sS] if x]
        show = df[display_cols].copy() if display_cols else df.copy()
        if c_rO and c_rS:
            try:
                show["ΔRank"] = pd.to_numeric(df[c_rS], errors="coerce") - pd.to_numeric(df[c_rO], errors="coerce")
            except Exception:
                pass
        if c_sO and c_sS:
            try:
                show["ΔScore"] = pd.to_numeric(df[c_sS], errors="coerce") - pd.to_numeric(df[c_sO], errors="coerce")
            except Exception:
                pass

        head = QLabel("Side-by-side ranking"); head.setObjectName("Sub")
        head.setStyleSheet("font-size:11px;letter-spacing:1px;text-transform:uppercase;")
        self._v.addWidget(head)
        tbl = QTableView(); tbl.setModel(_df_to_model(show.head(60)))
        tbl.horizontalHeader().setStretchLastSection(True); tbl.verticalHeader().setVisible(False)
        tbl.setMinimumHeight(360); tbl.setAlternatingRowColors(False)
        tbl.setStyleSheet("QTableView{alternate-background-color:transparent;gridline-color:transparent;}")
        self._v.addWidget(tbl, 1)

        # Top movers panel
        if c_sym and "ΔRank" in show.columns:
            movers = show[[c_sym, "ΔRank"]].dropna().copy()
            movers["ΔRank"] = pd.to_numeric(movers["ΔRank"], errors="coerce")
            movers = movers.dropna()
            if not movers.empty:
                up = movers.nsmallest(5, "ΔRank")   # negative delta = improved
                down = movers.nlargest(5, "ΔRank")
                mv_head = QLabel("Top movers"); mv_head.setObjectName("Sub")
                mv_head.setStyleSheet("font-size:11px;letter-spacing:1px;text-transform:uppercase;margin-top:6px;")
                self._v.addWidget(mv_head)
                grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(6)
                def _mover_card(sym, dr, tone):
                    card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", tone)
                    h = QHBoxLayout(card); h.setContentsMargins(12, 8, 12, 8)
                    s = QLabel(str(sym)); s.setStyleSheet("color:#fff;font-weight:700;font-size:13px;")
                    delta = QLabel(f"{'↑' if dr < 0 else '↓'} {abs(int(dr))}")
                    delta.setStyleSheet(f"color:{'#7FE0C6' if tone=='teal' else '#FF8597'};font-weight:700;")
                    h.addWidget(s); h.addStretch(); h.addWidget(delta)
                    return card
                for i, (_, r) in enumerate(up.iterrows()):
                    grid.addWidget(_mover_card(r[c_sym], r["ΔRank"], "teal"), i, 0)
                for i, (_, r) in enumerate(down.iterrows()):
                    grid.addWidget(_mover_card(r[c_sym], r["ΔRank"], "red"), i, 1)
                holder = QWidget(); holder.setLayout(grid)
                self._v.addWidget(holder)
        self._v.addStretch()



def _section_header(text: str) -> QLabel:
    lbl = QLabel(text); lbl.setObjectName("Sub")
    lbl.setStyleSheet("font-size:11px;letter-spacing:1.2px;text-transform:uppercase;color:#8A92A6;margin-top:6px;")
    return lbl


def _table_card(df: pd.DataFrame, accent: str = "indigo", max_rows: int = 200,
                min_height: int = 220) -> QFrame:
    card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", accent)
    v = QVBoxLayout(card); v.setContentsMargins(12, 10, 12, 12); v.setSpacing(6)
    if df is None or df.empty:
        v.addWidget(QLabel("No data available yet — run the pipeline."))
        return card
    tbl = QTableView(); tbl.setModel(_df_to_model(df.head(max_rows)))
    tbl.horizontalHeader().setStretchLastSection(True)
    tbl.verticalHeader().setVisible(False)
    tbl.setAlternatingRowColors(False)
    tbl.setMinimumHeight(min_height)
    v.addWidget(tbl)
    return card


def _empty_card(msg: str, accent: str = "dim") -> QFrame:
    card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", accent)
    v = QVBoxLayout(card); v.setContentsMargins(14, 12, 14, 12)
    lbl = QLabel(msg); lbl.setWordWrap(True); lbl.setStyleSheet("color:#B7BCC6;font-size:12.5px;")
    v.addWidget(lbl)
    return card


class PortfolioView(QWidget):
    """Sizing · Sector/Peers · Events+EV · Portfolio Validation verdict."""
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(10)
        self.body = QScrollArea(); self.body.setWidgetResizable(True); self.body.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget(); self._v = QVBoxLayout(self._holder)
        self._v.setContentsMargins(0, 0, 0, 0); self._v.setSpacing(12)
        self.body.setWidget(self._holder)
        outer.addWidget(self.body, 1)

    def _clear(self):
        while self._v.count():
            it = self._v.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def render(self, sizing_df: pd.DataFrame, sector_df: pd.DataFrame,
               events_df: pd.DataFrame, ev_df: pd.DataFrame,
               validation: dict):
        self._clear()

        # --- Portfolio validation verdict card (top) ---
        verdict = str((validation or {}).get("Batch_Verdict")
                      or (validation or {}).get("verdict") or "Unknown")
        vtone = {"Ship": "green", "Ship_With_Caveats": "amber",
                 "Downgrade_To_Watch": "red"}.get(verdict, "dim")
        vcard = QFrame(); vcard.setObjectName("Card"); vcard.setProperty("accent", vtone)
        vv = QVBoxLayout(vcard); vv.setContentsMargins(14, 12, 14, 12); vv.setSpacing(8)
        row = QHBoxLayout(); row.setSpacing(10)
        row.addWidget(QLabel("Portfolio Validation")); row.addWidget(_make_pill(verdict.replace("_", " "), vtone))
        row.addStretch()
        rw = QWidget(); rw.setLayout(row); vv.addWidget(rw)
        reasons = (validation or {}).get("reasons") or (validation or {}).get("caveats") or []
        if isinstance(reasons, (list, tuple)) and reasons:
            for r in reasons[:8]:
                lb = QLabel(f"• {r}"); lb.setWordWrap(True)
                lb.setStyleSheet("color:#DEE0E5;font-size:12.5px;")
                vv.addWidget(lb)
        elif not validation:
            vv.addWidget(QLabel("No portfolio_validation.json yet — run pipeline."))
        self._v.addWidget(vcard)

        # --- Sizing (Step 8) ---
        self._v.addWidget(_section_header("Position sizing · top5_sizing.csv  ·  inspired by Vibe Trading"))
        if sizing_df is None or sizing_df.empty:
            self._v.addWidget(_empty_card("No sizing output — Step 8 did not produce top5_sizing.csv.", "amber"))
        else:
            cols = [c for c in ("Symbol", "Price", "Weight_%", "Capital_INR", "Shares",
                                "Stop_Loss_INR", "Max_Loss_INR", "Max_Loss_%_of_NAV",
                                "Risk_Contribution_%") if c in sizing_df.columns]
            self._v.addWidget(_table_card(sizing_df[cols] if cols else sizing_df, "teal"))

        # --- Sector & Peers (Step 12) ---
        self._v.addWidget(_section_header("Sector & peer context · top5_sector_context.csv  ·  inspired by Fincept Terminal"))
        if sector_df is None or sector_df.empty:
            self._v.addWidget(_empty_card("No sector context available.", "amber"))
        else:
            self._v.addWidget(_table_card(sector_df, "violet"))

        # --- Events + EV merged (Steps 12/13) ---
        self._v.addWidget(_section_header("Event risk & expected value · top5_event_calendar.csv + top5_expected_value.csv  ·  inspired by Fincept Terminal + Vibe Trading"))
        merged = pd.DataFrame()
        if events_df is not None and not events_df.empty and ev_df is not None and not ev_df.empty:
            try:
                merged = events_df.merge(ev_df, on="Symbol", how="outer", suffixes=("", "_ev"))
            except Exception:
                merged = events_df.copy()
        elif events_df is not None and not events_df.empty:
            merged = events_df
        elif ev_df is not None and not ev_df.empty:
            merged = ev_df
        if merged.empty:
            self._v.addWidget(_empty_card("No event / EV data yet.", "amber"))
        else:
            self._v.addWidget(_table_card(merged, "amber"))
            in_window = 0
            if "Event_Risk_Flag" in merged.columns:
                try:
                    in_window = int((merged["Event_Risk_Flag"].astype(str) == "In_Window").sum())
                except Exception:
                    in_window = 0
            if in_window > 0:
                caution = QHBoxLayout(); caution.setSpacing(6)
                caution.addWidget(_make_pill(f"{in_window} in earnings window", "amber"))
                caution.addStretch()
                w = QWidget(); w.setLayout(caution); self._v.addWidget(w)

        self._v.addStretch()


class MacroRotationView(QWidget):
    """Institutional flow · Regime tilt · Rebalance diff."""
    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(10)
        self.body = QScrollArea(); self.body.setWidgetResizable(True); self.body.setFrameShape(QFrame.NoFrame)
        self._holder = QWidget(); self._v = QVBoxLayout(self._holder)
        self._v.setContentsMargins(0, 0, 0, 0); self._v.setSpacing(12)
        self.body.setWidget(self._holder)
        outer.addWidget(self.body, 1)

    def _clear(self):
        while self._v.count():
            it = self._v.takeAt(0)
            if it.widget(): it.widget().deleteLater()

    def render(self, inst_df: pd.DataFrame, macro_ctx: dict,
               regime_tilt: dict, rebalance: dict):
        self._clear()

        # --- Institutional flow (Step 14) ---
        fii_reg = str((macro_ctx or {}).get("fii_regime")
                      or (regime_tilt or {}).get("fii_regime") or "Unknown")
        ftone = {"Net_Buying": "green", "Net_Selling": "red", "Mixed": "amber"}.get(fii_reg, "dim")
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(QLabel("Institutional flow (FII/DII + bulk deals)  ·  inspired by Fincept Terminal"))
        head.addWidget(_make_pill(f"FII: {fii_reg.replace('_',' ')}", ftone))
        head.addStretch()
        hw = QWidget(); hw.setLayout(head); self._v.addWidget(hw)
        if inst_df is None or inst_df.empty:
            self._v.addWidget(_empty_card(
                "No institutional flow output. Drop fii_dii_daily.csv and bulk_deals.csv "
                "into data/ then re-run to activate.", "amber"))
        else:
            self._v.addWidget(_table_card(inst_df, "blue"))

        # --- Regime tilt (Step 15) ---
        self._v.addWidget(_section_header("Regime tilt · regime_tilt_report.json  ·  inspired by Vibe Trading"))
        if not regime_tilt:
            self._v.addWidget(_empty_card("No regime tilt report yet.", "amber"))
        else:
            regime = str(regime_tilt.get("regime", "NEUTRAL"))
            rtone = {"RISK_ON": "green", "RISK_OFF": "red", "NEUTRAL": "blue"}.get(regime, "dim")
            applied = bool(regime_tilt.get("applied_to_scoring"))
            atone = "green" if applied else "dim"
            atxt = "APPLIED" if applied else "REPORT-ONLY"
            card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", rtone)
            cv = QVBoxLayout(card); cv.setContentsMargins(14, 12, 14, 12); cv.setSpacing(8)
            row = QHBoxLayout(); row.setSpacing(8)
            row.addWidget(QLabel("Regime")); row.addWidget(_make_pill(regime, rtone))
            row.addSpacing(10); row.addWidget(_make_pill(atxt, atone))
            row.addStretch()
            rw = QWidget(); rw.setLayout(row); cv.addWidget(rw)
            fam = regime_tilt.get("family_multipliers") or {}
            if fam:
                fg = QHBoxLayout(); fg.setSpacing(6)
                for k, v in fam.items():
                    tone = "green" if v and v > 1 else ("amber" if v and v < 1 else "dim")
                    fg.addWidget(_make_pill(f"{k}: ×{v}", tone))
                fg.addStretch()
                fw = QWidget(); fw.setLayout(fg); cv.addWidget(fw)
            n = regime_tilt.get("n_survivors")
            if n is not None:
                cv.addWidget(QLabel(f"Survivors reweighted: {n}"))
            notes = regime_tilt.get("notes")
            if notes:
                nl = QLabel(str(notes)); nl.setWordWrap(True)
                nl.setStyleSheet("color:#B7BCC6;font-size:12px;")
                cv.addWidget(nl)
            self._v.addWidget(card)

        # --- Rebalance diff (Step 16) ---
        self._v.addWidget(_section_header("Rebalance diff · rebalance_diff.json  ·  inspired by Vibe Trading"))
        if not rebalance:
            self._v.addWidget(_empty_card("No rebalance diff yet.", "amber"))
        else:
            rec = str(rebalance.get("recommendation", "Unknown"))
            rtone = ("green" if rec.startswith("Rotate_Edge") else
                     "amber" if rec.startswith("Rotate") or rec.startswith("Hold_Cost") else
                     "blue" if rec.startswith("Hold") else "dim")
            card = QFrame(); card.setObjectName("Card"); card.setProperty("accent", rtone)
            cv = QVBoxLayout(card); cv.setContentsMargins(14, 12, 14, 12); cv.setSpacing(8)
            row = QHBoxLayout(); row.setSpacing(8)
            row.addWidget(QLabel("Recommendation")); row.addWidget(_make_pill(rec.replace("_", " "), rtone))
            turn = rebalance.get("estimated_turnover_%")
            if turn is not None:
                row.addSpacing(10); row.addWidget(_make_pill(f"Turnover: {turn}%", "violet"))
            edge = rebalance.get("net_edge_after_cost_%")
            if edge is not None:
                et = "green" if isinstance(edge, (int, float)) and edge > 0 else "red"
                row.addSpacing(6); row.addWidget(_make_pill(f"Net edge: {edge}%", et))
            row.addStretch()
            rw = QWidget(); rw.setLayout(row); cv.addWidget(rw)

            def _chip_row(label: str, items: list, tone: str):
                if not items: return
                cv.addWidget(_section_header(f"{label} ({len(items)})"))
                fg = QHBoxLayout(); fg.setSpacing(6)
                for s in items[:20]:
                    fg.addWidget(_make_pill(str(s), tone))
                fg.addStretch()
                fw = QWidget(); fw.setLayout(fg); cv.addWidget(fw)

            _chip_row("Holds", rebalance.get("holds") or [], "blue")
            _chip_row("Exits", rebalance.get("exits") or [], "red")
            _chip_row("Entries", rebalance.get("entries") or [], "green")
            reasons = rebalance.get("exit_reasons") or {}
            if reasons:
                cv.addWidget(_section_header("Exit reasons"))
                for sym, why in reasons.items():
                    lb = QLabel(f"• {sym}: {why}"); lb.setWordWrap(True)
                    lb.setStyleSheet("color:#DEE0E5;font-size:12.5px;")
                    cv.addWidget(lb)
            self._v.addWidget(card)

        self._v.addStretch()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"NSE Quant Engine — v{APP_VERSION}")
        self.resize(1480, 920)

        central = QWidget(); central.setObjectName("AppRoot"); self.setCentralWidget(central)
        outer = QHBoxLayout(central); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)

        # ---- Left: main column (top bar + tabs) ----
        left = QWidget(); left.setObjectName("MainColumn")
        col = QVBoxLayout(left); col.setContentsMargins(20, 16, 16, 12); col.setSpacing(12)

        # Top bar
        topbar = QFrame(); topbar.setObjectName("TopBar")
        tb = QHBoxLayout(topbar); tb.setContentsMargins(18, 12, 14, 12); tb.setSpacing(12)
        title = QLabel("NSE Quant Engine"); title.setObjectName("Header")
        self._version_pill = QLabel(f"v{APP_VERSION}"); self._version_pill.setObjectName("Pill")
        pill = self._version_pill
        self.lbl_lastrun = QLabel("no runs yet"); self.lbl_lastrun.setObjectName("PillAge")
        tb.addWidget(title); tb.addSpacing(8); tb.addWidget(pill)
        tb.addSpacing(8); tb.addWidget(self.lbl_lastrun)
        tb.addStretch()

        self.cb_shadow = QCheckBox("Include shadow"); self.cb_shadow.setChecked(True)
        self.cb_fetch  = QCheckBox("Refresh data");   self.cb_fetch.setChecked(True)
        tb.addWidget(self.cb_shadow); tb.addWidget(self.cb_fetch)

        self.btn_reload = QPushButton("⟳ Reload last run"); self.btn_reload.setObjectName("Ghost")
        self.btn_browser = QPushButton("Open in browser ↗"); self.btn_browser.setObjectName("Ghost")
        self.btn_evidence = QPushButton("📦 Evidence zip"); self.btn_evidence.setObjectName("Ghost")
        self.btn_evidence.setToolTip("Reveal the newest evidence bundle (hand this zip to Claude / any LLM)")
        self.btn_run = QPushButton("▶  Run Full Pipeline"); self.btn_run.setObjectName("Primary")
        self.btn_drawer = QToolButton(); self.btn_drawer.setObjectName("Drawer")
        self.btn_drawer.setText("☰  Activity")
        self.btn_drawer.setToolTip("Toggle run activity drawer (F9)")
        tb.addWidget(self.btn_reload); tb.addWidget(self.btn_browser); tb.addWidget(self.btn_evidence)
        tb.addWidget(self.btn_run); tb.addWidget(self.btn_drawer)
        col.addWidget(topbar)

        # Tabs
        self.tabs = QTabWidget()
        self.dashboard = Dashboard()
        self.tab_scores = self._make_table_tab()
        self.tab_shadow = self._make_table_tab()
        self.tab_compare = CompareView()
        self.tab_dq = DQReportView()
        self.tab_validation = ValidationView()
        self.tab_trade = TradePlanView()
        self.tab_portfolio = PortfolioView()
        self.tab_macro = MacroRotationView()
        self.tabs.addTab(self.dashboard, "Dashboard")
        # Embedded HTML dashboard (Chart.js) — only shown when WebEngine is available.
        self.tab_html = HtmlDashboardView() if HAS_WEBENGINE else None
        if self.tab_html is not None:
            self.tabs.addTab(self.tab_html, "Dashboard (HTML)")
        for tab, name in [
            (self.tab_scores, "Scores"), (self.tab_shadow, "Shadow"),
            (self.tab_compare, "Compare"), (self.tab_dq, "DQ Report"),
            (self.tab_validation, "Validation"), (self.tab_trade, "Trade Plan"),
            (self.tab_portfolio, "Portfolio"), (self.tab_macro, "Macro & Rotation"),
        ]:
            self.tabs.addTab(tab, name)
        col.addWidget(self.tabs, 1)

        outer.addWidget(left, 1)

        # ---- Right: collapsible drawer ----
        self.drawer = RunDrawer()
        outer.addWidget(self.drawer)
        self.dashboard.set_console_callback(self.drawer.append_log)

        # Status bar
        self.status = QStatusBar(); self.setStatusBar(self.status)
        self.status.showMessage("Ready.")

        # Wire up
        self.btn_run.clicked.connect(self.start_run)
        self.btn_reload.clicked.connect(lambda: self.load_last_run(refresh_tabs=True))
        self.btn_browser.clicked.connect(self.dashboard.open_browser)
        self.btn_evidence.clicked.connect(self._reveal_evidence_zip)
        self.btn_drawer.clicked.connect(self.drawer.toggle)
        QShortcut(QKeySequence("F9"), self, activated=self.drawer.toggle)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.load_last_run)

        self.bridge = LogBridge()
        self.bridge.line.connect(self.drawer.append_log)
        self.bridge.step.connect(self._on_step)
        self.thread: RunnerThread | None = None

        # Boot: show whatever the last run produced.
        self.load_last_run()
        # Regression guard — log to Activity drawer, never crash the app.
        try:
            self._startup_self_check()
        except Exception as e:
            _log_crash(f"self-check failed: {e}")

    def _startup_self_check(self):
        """Post-boot: verify version pill matches APP_VERSION and, once the
        WebEngine dashboard finishes loading, check that the expected v4.8
        dashboard blocks are present. Findings go to the Activity drawer."""
        want = f"v{APP_VERSION}"
        got = self._version_pill.text()
        if got != want:
            _log_crash(f"[self-check] header pill mismatch: expected {want!r}, got {got!r}")
        else:
            self.drawer.append_log(f"[self-check] header pill OK ({got})")
        view = getattr(self.dashboard, "view", None)
        if view is None:
            return
        expected_ids = ("maturityCards", "readiness", "universeChart", "shadowCards")

        def _after_load(ok: bool):
            if not ok:
                self.drawer.append_log("[self-check] dashboard loadFinished(ok=False)")
                return
            js = (
                "JSON.stringify({"
                + ",".join(f"{i}: !!document.getElementById('{i}')" for i in expected_ids)
                + "})"
            )
            def _cb(result):
                try:
                    missing = [k for k, v in (json.loads(result) if result else {}).items() if not v]
                except Exception:
                    missing = list(expected_ids)
                if missing:
                    self.drawer.append_log(f"[self-check] dashboard missing blocks: {', '.join(missing)}")
                else:
                    self.drawer.append_log("[self-check] dashboard blocks OK")
            try:
                view.page().runJavaScript(js, 0, _cb)
            except Exception as e:
                self.drawer.append_log(f"[self-check] JS probe failed: {e}")

        try:
            view.loadFinished.connect(_after_load)
        except Exception:
            pass

    # ----- tab builders -----
    def _make_table_tab(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0)
        tv = QTableView(); tv.horizontalHeader().setStretchLastSection(True)
        tv.verticalHeader().setVisible(False)
        l.addWidget(tv); w.table = tv
        return w

    def _make_text_tab(self):
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0)
        t = QTextEdit(); t.setReadOnly(True); l.addWidget(t); w.text = t
        return w

    # ----- live run hooks -----
    def _on_step(self, info: dict):
        self.drawer.update_step(info["name"], info["status"], info["duration_s"])
        # Do not refresh the dashboard on every step. On Windows, repeated
        # dashboard reloads after long runs can trigger native Qt/WebEngine
        # access violations outside Python exception handling. The final reload
        # happens once in _on_done.

    def start_run(self):
        if self.thread and self.thread.isRunning():
            return
        self.drawer.log.clear()
        self.drawer.set_status("running…")
        self.status.showMessage("Running… (shadow waits for normal run to finish)")
        self.btn_run.setEnabled(False)
        steps = orchestrator.build_steps(
            include_shadow=self.cb_shadow.isChecked(),
            include_fetch=self.cb_fetch.isChecked(),
        )
        self.drawer.reset_steps([s.name for s in steps])
        if not self.drawer.is_open():
            self.drawer.set_open(True)
        self.thread = RunnerThread(
            steps,
            self.bridge,
            include_shadow=self.cb_shadow.isChecked(),
            include_fetch=self.cb_fetch.isChecked(),
        )
        self.thread.done.connect(self._on_done)
        self.thread.start()

    def _on_done(self, summary: dict):
        self.btn_run.setEnabled(True)
        steps = summary.get("steps", []) or []
        ok = sum(1 for s in steps if s.get("status") == "ok")
        bad = sum(1 for s in steps if s.get("status") == "error")
        skp = sum(1 for s in steps if s.get("status") == "skipped")
        msg = f"Done in {summary.get('duration_s', 0)}s — {ok} ok, {skp} skipped, {bad} errors."
        self.status.showMessage(msg)
        self.drawer.set_status("idle")
        try:
            # First refresh only lightweight header/dashboard state. Heavy report
            # tabs are delayed so completion of a long child run cannot trigger a
            # native Qt teardown during the done callback.
            self.load_last_run(refresh_tabs=False)
            QTimer.singleShot(900, lambda: self.load_last_run(refresh_tabs=True))
        except Exception as e:
            _log_crash(f"Final reload after run failed but app stayed open: {type(e).__name__}: {e}")

    def _reveal_evidence_zip(self):
        """Reveal the newest evidence bundle zip in the OS file browser.
        This is the artifact the user hands to Claude / any LLM for rationale."""
        try:
            zips = sorted(OUT.glob("evidence_bundle_*.zip"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
            if not zips:
                # fallback: try any zip in output/
                zips = sorted(OUT.glob("*.zip"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
            if not zips:
                QMessageBox.information(
                    self, "No evidence zip yet",
                    "No evidence_bundle_*.zip found in output/. Run the pipeline first — "
                    "the bundle is written by Step 11 (evidence_bundle).")
                return
            target = zips[0]
            self.status.showMessage(f"Revealing {target.name}")
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target.parent)])
        except Exception as e:
            _log_crash(f"Reveal evidence zip failed: {e}")
            QMessageBox.warning(self, "Could not open", f"{type(e).__name__}: {e}")

    def closeEvent(self, event):
        # Guard against Qt/WebEngine-driven close attempts while a run is live.
        if self.thread and self.thread.isRunning():
            resp = QMessageBox.question(
                self, "Run in progress",
                "The pipeline is still running. Quit anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if resp != QMessageBox.Yes:
                event.ignore(); return
        event.accept()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    # ----- persistent last-run loading -----
    def load_last_run(self, refresh_tabs: bool = True):
        """Read output/run_manifest.json (if present) and rehydrate the UI."""
        manifest_path = OUT / "run_manifest.json"
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text())
            except Exception:
                manifest = {}

        # header age pill
        finished = manifest.get("completed_at")
        if finished:
            champ = manifest.get("champion", "official").title()
            self.lbl_lastrun.setText(f"last run: {_human_age(finished)} · champion: {champ}")
        else:
            self.lbl_lastrun.setText("no runs yet — click ▶ Run Full Pipeline")

        if not refresh_tabs:
            try:
                self.dashboard.refresh()
            except Exception as e:
                _log_crash(f"Dashboard refresh failed: {e}")
            return

        # tabs (CSV / text)
        def load_csv(p: Path):
            try:
                return pd.read_csv(p) if p.exists() else pd.DataFrame()
            except Exception:
                return pd.DataFrame()
        self.tab_scores.table.setModel(_df_to_model(load_csv(OUT / "latest_scores.csv").head(250)))
        self.tab_shadow.table.setModel(_df_to_model(load_csv(OUT / "latest_scores_v4_shadow.csv").head(250)))
        cmp_df = load_csv(OUT / "shadow_vs_official.csv")
        # Structured tabs (DQ, Validation, Trade Plan)
        def _safe_json(p: Path) -> dict:
            if not p.exists(): return {}
            try:
                return json.loads(p.read_text(encoding="utf-8").replace(": NaN", ": null"))
            except Exception:
                return {}
        def _safe_text(p: Path) -> str:
            try:
                return p.read_text(encoding="utf-8") if p.exists() else ""
            except Exception:
                return ""

        dq_summary = _safe_json(DATA / "dq_summary.json")
        quality_df = load_csv(OUT / "etf_quality_latest.csv")
        if quality_df.empty:
            quality_df = load_csv(DATA / "etf_quality_latest.csv")
        val_status = _safe_json(OUT / "validation_status.json")
        val_report = _safe_text(OUT / "cross_sectional_validation_report.md")
        trade_df = load_csv(OUT / "trade_plan_latest.csv")
        trade_report = _safe_text(OUT / "trade_plan_report.md")
        cmp_json = _safe_json(OUT / "shadow_vs_official.json")
        try:
            self.tab_dq.render(dq_summary, quality_df)
        except Exception as e:
            _log_crash(f"DQ tab render failed: {e}")
        try:
            self.tab_validation.render(val_status, val_report)
        except Exception as e:
            _log_crash(f"Validation tab render failed: {e}")
        try:
            self.tab_trade.render(trade_df, val_status, trade_report)
        except Exception as e:
            _log_crash(f"Trade Plan tab render failed: {e}")
        try:
            self.tab_compare.render(cmp_df, cmp_json)
        except Exception as e:
            _log_crash(f"Compare tab render failed: {e}")

        # New tabs: Portfolio + Macro & Rotation (Steps 8, 10–16)
        sizing_df   = load_csv(OUT / "top5_sizing.csv")
        sector_df   = load_csv(OUT / "top5_sector_context.csv")
        events_df   = load_csv(OUT / "top5_event_calendar.csv")
        ev_df       = load_csv(OUT / "top5_expected_value.csv")
        inst_df     = load_csv(OUT / "top5_institutional_flow.csv")
        pv_json     = _safe_json(OUT / "portfolio_validation.json")
        macro_json  = _safe_json(OUT / "macro_context.json")
        tilt_json   = _safe_json(OUT / "regime_tilt_report.json")
        rebal_json  = _safe_json(OUT / "rebalance_diff.json")
        try:
            self.tab_portfolio.render(sizing_df, sector_df, events_df, ev_df, pv_json)
        except Exception as e:
            _log_crash(f"Portfolio tab render failed: {e}")
        try:
            self.tab_macro.render(inst_df, macro_json, tilt_json, rebal_json)
        except Exception as e:
            _log_crash(f"Macro tab render failed: {e}")

        # drawer: hydrate step list from last run so users can see what ran
        steps = manifest.get("steps") or []
        if steps and not (self.thread and self.thread.isRunning()):
            self.drawer.reset_steps([s["name"] for s in steps])
            for s in steps:
                self.drawer.update_step(s["name"], s["status"], s.get("duration_s", 0.0))
            self.drawer.set_status(f"last run · {finished or ''}")

        # dashboard (HTML)
        try:
            self.dashboard.refresh()
        except Exception as e:
            _log_crash(f"Dashboard refresh failed: {e}")


def main():
    global _fault_log_fh
    _install_global_hooks()
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        fault_path = OUT / "last_crash.log"
        _fault_log_fh = fault_path.open("a", encoding="utf-8")
        _fault_log_fh.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] starting app with faulthandler enabled\n")
        _fault_log_fh.flush()
        faulthandler.enable(file=_fault_log_fh, all_threads=True)
    except Exception:
        _fault_log_fh = None
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    app.setQuitOnLastWindowClosed(False)
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor("#0A0B12"))
    pal.setColor(QPalette.Base, QColor("#0A0B12"))
    pal.setColor(QPalette.Text, QColor("#ECEDEE"))
    pal.setColor(QPalette.WindowText, QColor("#ECEDEE"))
    pal.setColor(QPalette.Highlight, QColor("#D8345F"))
    app.setPalette(pal)

    global _crash_bridge
    _crash_bridge = _AppCrashBridge()
    # Anchor the window on the QApplication so Python GC cannot silently drop
    # it (a common cause of "app closed on its own" symptoms on some Windows
    # + PySide6 + WebEngine combinations).
    w = MainWindow()
    app._mainwindow_ref = w  # type: ignore[attr-defined]
    _crash_bridge.line.connect(w.drawer.append_log)
    # flush anything captured before the drawer existed
    for m in _early_log:
        w.drawer.append_log(m)
    _early_log.clear()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:
        tb = traceback.format_exc()
        _write_crash_log(f"[startup fatal] {type(exc).__name__}: {exc}\n{tb}")
        print(f"\n*** Startup fatal: {type(exc).__name__}: {exc}\n{tb}", file=sys.stderr)
        try:
            from PySide6.QtWidgets import QApplication as _QA, QMessageBox as _MB
            _app = _QA.instance() or _QA(sys.argv)
            _MB.critical(None, "NSE Quant Engine crashed",
                         f"{type(exc).__name__}: {exc}\n\nDetails written to output/last_crash.log")
        except Exception:
            pass
        sys.exit(1)

