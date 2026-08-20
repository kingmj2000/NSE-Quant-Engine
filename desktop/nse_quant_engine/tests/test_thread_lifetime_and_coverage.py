"""Background-thread lifetime and daily-flow coverage regressions.

CRASH BACKGROUND
----------------
`last_crash.log` shows the sequence:

    [qt-fatal]  QThread: Destroyed while thread '' is still running
    ...
    Windows fatal exception: code 0xc0000374        (heap corruption)
    Windows fatal exception: access violation       (0xc0000005)

Two paths destroyed a live QThread:

  1. Clicking "Refresh optional feeds now" twice reassigned
     `self._refresh_thread`, dropping the only reference to a thread that was
     still running. Python collected it mid-run.
  2. `closeEvent` waited only for the pipeline thread, so quitting the app while
     a refresh was in flight destroyed that thread on the way out.

Both were latent until manual refresh started forcing a real fetch: the thread
went from returning in milliseconds to living for minutes.

Heap corruption surfaces later, somewhere unrelated — which is why the log shows
the fatal exception inside `read_text` and `_cache_row_count` rather than at the
actual fault.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pandas as pd

from core import optional_data_fetchers as odf

ENG = Path(__file__).resolve().parents[1]


# ── thread lifetime ─────────────────────────────────────────────────────────

def _src(rel: str) -> str:
    return (ENG / rel).read_text(encoding="utf-8")


def test_refresh_is_guarded_against_overlapping_clicks():
    for rel in ("ui/decision_center.py", "run_app.py"):
        src = _src(rel)
        i = src.index("def _refresh_optional_feeds")
        body = src[i:i + 3000]
        assert "isRunning()" in body, (
            f"{rel}: no re-entrancy guard — a second click drops a live QThread")
        assert "return" in body.split("isRunning()")[1][:400], (
            f"{rel}: guard does not actually bail out")


def test_thread_is_deleted_only_after_it_finishes():
    for rel in ("ui/decision_center.py", "run_app.py"):
        src = _src(rel)
        assert "finished.connect" in src and "deleteLater" in src, (
            f"{rel}: thread must be released by Qt on finished, not by rebinding")


def test_both_windows_expose_a_wait_hook():
    for rel in ("ui/decision_center.py", "run_app.py"):
        assert "def wait_for_background_work" in _src(rel), rel


def test_close_event_waits_for_every_background_thread():
    src = _src("run_app.py")
    i = src.index("def closeEvent")
    body = src[i:src.index("def ", i + 20)]
    assert "wait_for_background_work" in body, (
        "closeEvent waited only for the pipeline thread; the refresh threads were "
        "destroyed by app exit, which is the qt-fatal in the crash log")
    assert "_background_thread_owners" in body


def test_wait_hook_reports_idle_when_no_thread_exists():
    """The hook must be safe to call before any refresh has ever run."""
    src = _src("ui/decision_center.py")
    i = src.index("def wait_for_background_work")
    body = src[i:i + 700]
    assert "is None" in body and "return True" in body


# ── daily-flow coverage reporting ───────────────────────────────────────────

def _write_flow(tmp_path: Path, dates: list[str]) -> Path:
    p = tmp_path / "fii_dii_daily.csv"
    pd.DataFrame({
        "Date": dates,
        "FII_Net_INR_Cr": [1.0] * len(dates),
        "DII_Net_INR_Cr": [2.0] * len(dates),
    }).to_csv(p, index=False)
    return p


def test_gappy_flow_series_is_reported(tmp_path, capsys=None):
    import contextlib
    import io

    # 16 rows across ~28 business days — the real shape of the observed cache.
    dates = ["2026-07-09", "2026-07-10", "2026-07-13", "2026-07-16", "2026-07-20",
             "2026-07-22", "2026-07-24", "2026-07-27", "2026-07-31", "2026-08-04",
             "2026-08-06", "2026-08-07", "2026-08-11", "2026-08-14", "2026-08-17"]
    target = _write_flow(tmp_path, dates)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        odf._report_flow_coverage("fii_dii", target, backfill_ok=False)
    out = buf.getvalue()

    assert "[gap]" in out
    assert "business days" in out
    assert "backfill source was unavailable" in out, (
        "when the backfill source failed, say the gaps will persist")


def test_complete_series_is_not_flagged(tmp_path):
    import contextlib
    import io

    dates = [d.strftime("%Y-%m-%d")
             for d in pd.bdate_range("2026-08-03", "2026-08-14")]
    target = _write_flow(tmp_path, dates)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        odf._report_flow_coverage("fii_dii", target, backfill_ok=True)
    assert "of" not in buf.getvalue().replace("coverage check", "")


def test_small_gaps_are_tolerated_as_holidays(tmp_path):
    """NSE holidays are not modelled, so one or two missing days must not shout."""
    import contextlib
    import io

    dates = [d.strftime("%Y-%m-%d")
             for d in pd.bdate_range("2026-08-03", "2026-08-28")]
    dates.remove("2026-08-14")
    target = _write_flow(tmp_path, dates)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        odf._report_flow_coverage("fii_dii", target, backfill_ok=True)
    assert "business days\n" not in buf.getvalue()


def test_coverage_check_never_raises_on_a_broken_file(tmp_path):
    p = tmp_path / "fii_dii_daily.csv"
    p.write_bytes(b"\x00\x01 not a csv \xff")
    odf._report_flow_coverage("fii_dii", p, backfill_ok=True)  # must not raise

    missing = tmp_path / "nope.csv"
    odf._report_flow_coverage("fii_dii", missing, backfill_ok=True)
