"""Tests for core.cleanup_outputs — retention + protected files + escape hatch."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from core import cleanup_outputs as CO  # noqa: E402


def _touch(p: Path, mtime: float) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    import os
    os.utime(p, (mtime, mtime))


def _setup_project(tmp_path: Path) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    now = time.time()
    for i in range(15):
        _touch(out / f"dashboard_2026-06-{i+1:02d}.html", now - (15 - i) * 100)
        _touch(out / f"latest_scores_2026-06-{i+1:02d}.xlsx", now - (15 - i) * 100)
    # Protected files (must survive)
    for name in [
        "dashboard_latest.html", "latest_scores.csv",
        "score_history.csv", "signal_history.csv",
        "forward_return_history.csv", "alpha_score_history.csv",
        "cross_sectional_spread_by_date.csv",
        "shadow_vs_official_history.csv",
    ]:
        _touch(out / name, now)
    # Enricher backups at project root
    for i in range(15):
        _touch(tmp_path / f"manual_etf_quality_backup_2026060{i%10}_{i:06d}.csv",
               now - (15 - i) * 100)
    # TER debug xlsx
    dbg = tmp_path / "data" / "ter_tracking_debug"
    for i in range(15):
        _touch(dbg / f"tracking_error_raw_{i:06d}.xlsx", now - (15 - i) * 100)
    return tmp_path


def test_prunes_dated_files_to_keep_n(tmp_path, monkeypatch):
    _setup_project(tmp_path)
    from core import config as C
    monkeypatch.setattr(C, "RETENTION_KEEP_N", 10, raising=False)

    res = CO.run_cleanup(tmp_path)
    assert res["status"] == "ok"

    out = tmp_path / "output"
    dashboards = sorted(out.glob("dashboard_2026-*.html"))
    xlsxs = sorted(out.glob("latest_scores_2026-*.xlsx"))
    assert len(dashboards) == 10
    assert len(xlsxs) == 10
    # Newest kept
    assert (out / "dashboard_2026-06-15.html").exists()
    assert not (out / "dashboard_2026-06-01.html").exists()

    backups = sorted(tmp_path.glob("manual_etf_quality_backup_*.csv"))
    assert len(backups) == 10
    debugs = sorted((tmp_path / "data" / "ter_tracking_debug").glob("*.xlsx"))
    assert len(debugs) == 10

    # All protected files still there
    for name in CO.PROTECTED_FILES:
        p = out / name
        if p.exists() or name in {"dashboard_latest.html", "latest_scores.csv",
                                  "score_history.csv", "signal_history.csv",
                                  "forward_return_history.csv",
                                  "alpha_score_history.csv",
                                  "cross_sectional_spread_by_date.csv",
                                  "shadow_vs_official_history.csv"}:
            assert (out / name).exists(), name

    # Audit log exists with header + rows
    log = out / "cleanup_log.csv"
    assert log.exists()
    text = log.read_text()
    assert "timestamp,root,pattern,kept,removed,removed_files" in text
    assert "dashboard_*.html" in text


def test_keep_n_zero_disables(tmp_path, monkeypatch):
    _setup_project(tmp_path)
    from core import config as C
    monkeypatch.setattr(C, "RETENTION_KEEP_N", 0, raising=False)

    CO.run_cleanup(tmp_path)
    out = tmp_path / "output"
    assert len(list(out.glob("dashboard_2026-*.html"))) == 15
    assert len(list(out.glob("latest_scores_2026-*.xlsx"))) == 15
    log = (out / "cleanup_log.csv").read_text()
    assert "disabled" in log


def test_protected_files_never_removed_even_if_pattern_matches(tmp_path, monkeypatch):
    """A protected filename that happens to match a prune glob must survive."""
    out = tmp_path / "output"
    out.mkdir()
    now = time.time()
    # Fake: pretend dashboard_latest.html matches dashboard_*.html glob (it does).
    _touch(out / "dashboard_latest.html", now - 10_000)  # oldest
    for i in range(12):
        _touch(out / f"dashboard_2026-06-{i+1:02d}.html", now - (12 - i) * 100)

    from core import config as C
    monkeypatch.setattr(C, "RETENTION_KEEP_N", 5, raising=False)
    CO.run_cleanup(tmp_path)

    assert (out / "dashboard_latest.html").exists(), "protected file was pruned!"
    dated = sorted(out.glob("dashboard_2026-*.html"))
    assert len(dated) == 5
