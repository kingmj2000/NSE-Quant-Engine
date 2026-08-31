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

import ast
import re
from pathlib import Path

import pandas as pd

from core import optional_data_fetchers as odf

ENG = Path(__file__).resolve().parents[1]


# ── thread lifetime ─────────────────────────────────────────────────────────

def _src(rel: str) -> str:
    return (ENG / rel).read_text(encoding="utf-8")


WINDOWS = ("ui/decision_center.py", "run_app.py")


def _func(rel: str, name: str) -> ast.FunctionDef:
    """Return the parsed FunctionDef, so assertions see code and not comments.

    The previous version of this module sliced the raw source text and searched
    for literal substrings. That is the anti-pattern this project has been bitten
    by repeatedly: the surrounding comments must contain the very words being
    searched for, so the assertion could not fail, and one of the assertions
    below was inverted — it REQUIRED the crash pattern to be present.
    """
    tree = ast.parse(_src(rel))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    raise AssertionError(f"{rel}: {name}() is missing")


def _base_names(cls: ast.ClassDef) -> set[str]:
    out: set[str] = set()
    for b in cls.bases:
        if isinstance(b, ast.Name):
            out.add(b.id)
        elif isinstance(b, ast.Attribute):
            out.add(b.attr)
    return out


def test_refresh_is_guarded_against_overlapping_clicks():
    for rel in WINDOWS:
        fn = _func(rel, "_refresh_optional_feeds")
        nodes = list(ast.walk(fn))
        called = {n.func.attr for n in nodes
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert called & {"is_alive", "isRunning"}, (
            f"{rel}: no liveness check — a second click abandons a running worker")
        assert any(isinstance(n, ast.Return) for n in nodes), (
            f"{rel}: the guard does not bail out")


def test_refresh_worker_is_never_a_qt_object_deleted_underneath_us():
    """The crash: one worker owned by two deallocators.

    `t = _RefreshThread(self)` kept the Python wrapper alive in
    `self._refresh_thread` while `t.finished.connect(t.deleteLater)` handed the
    C++ object to Qt. After the first refresh completed, the next click and
    `wait_for_background_work()` both called a method on a freed object —
    0xc0000005, or heap corruption 0xc0000374 surfacing later somewhere
    unrelated. A plain `threading.Thread` has no C++ half and cannot enter this
    state.

    Scoped to this one function on purpose: `RunnerThread` (the pipeline
    QThread) is legitimate and must not be caught here.
    """
    for rel in WINDOWS:
        fn = _func(rel, "_refresh_optional_feeds")
        nodes = list(ast.walk(fn))
        qthreads = [c.name for c in nodes
                    if isinstance(c, ast.ClassDef) and "QThread" in _base_names(c)]
        assert not qthreads, (
            f"{rel}: manual refresh builds {qthreads} — a QThread here is owned by "
            f"both Python and Qt")
        deferred_delete = [n for n in nodes
                           if isinstance(n, ast.Call)
                           and isinstance(n.func, ast.Attribute)
                           and n.func.attr == "connect"
                           and any(isinstance(a, ast.Attribute)
                                   and a.attr == "deleteLater" for a in n.args)]
        assert not deferred_delete, (
            f"{rel}: finished->deleteLater frees the C++ object while "
            f"self._refresh_thread still points at the wrapper")


def test_both_windows_expose_a_wait_hook():
    for rel in WINDOWS:
        _func(rel, "wait_for_background_work")  # raises with the file name if absent


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
    fn = _func("ui/decision_center.py", "wait_for_background_work")
    nodes = list(ast.walk(fn))
    compares_to_none = any(
        isinstance(n, ast.Compare)
        and any(isinstance(c, ast.Constant) and c.value is None for c in n.comparators)
        for n in nodes)
    returns_true = any(
        isinstance(n, ast.Return) and isinstance(n.value, ast.Constant)
        and n.value.value is True for n in nodes)
    assert compares_to_none, "no None check — first call would raise before any refresh"
    assert returns_true, "must report idle explicitly rather than falling through"


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

    # Behaviour, not phrasing: a gap must be flagged, name the feed, and carry a
    # day count. Asserting an exact sentence has broken this test five times.
    assert "[gap]" in out
    assert "fii_dii" in out
    assert re.search(r"\d+\s+(of\s+\d+\s+)?business day", out), out
    # backfill was unavailable, so the report must say more than the gap itself
    assert len(out.strip().splitlines()) >= 2, out


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
