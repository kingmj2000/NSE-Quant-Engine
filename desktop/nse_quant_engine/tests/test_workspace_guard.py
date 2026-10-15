"""The app must say loudly when it is running in a folder with no history.

2026-10-15 17:34: launched from the git clone instead of the run folder. All 20
steps reported ok; Data Health read 0/100 and validation looked wiped. Nothing
was broken — the clone has no history. A silent empty workspace is a check that
could not be performed, reported as a result.
"""
from __future__ import annotations

from pathlib import Path

from core import workspace as ws


def _rows(p: Path, n: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("h\n" + "x\n" * n, encoding="utf-8")


def test_empty_git_clone_is_flagged(tmp_path):
    (tmp_path / ".git").mkdir()
    eng = tmp_path / "desktop" / "nse_quant_engine"
    eng.mkdir(parents=True)
    _rows(eng / "output" / "score_history.csv", 550)        # one run's worth
    info = ws.inspect(eng)
    assert info["git_checkout"] is True and info["has_history"] is False
    msg = ws.warning(eng)
    assert msg and "GIT CLONE" in msg and "etf_metadata_imports" in msg


def test_real_run_folder_is_quiet(tmp_path):
    _rows(tmp_path / "output" / "score_history.csv", 5000)
    _rows(tmp_path / "output" / "forward_return_history.csv", 5000)
    (tmp_path / "data" / "etf_metadata_imports").mkdir(parents=True)
    (tmp_path / "data" / "etf_metadata_imports" / "auto_amfi_aum_latest.csv").write_text("a\n1\n")
    assert ws.warning(tmp_path) is None


def test_warning_is_wired_into_app_and_pipeline():
    root = Path(__file__).resolve().parents[1]
    assert "workspace.warning(BASE)" in (root / "run_app.py").read_text(encoding="utf-8").replace("_ws.", "workspace.")
    assert "workspace.warning(BASE)" in (root / "orchestrator.py").read_text(encoding="utf-8").replace("_ws.", "workspace.")
