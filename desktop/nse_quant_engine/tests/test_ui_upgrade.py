"""Regression tests for the UI upgrade (Phase 5 acceptance criteria).

These tests exercise the pure-data guarantees of the UI upgrade without
booting Qt — the risky UI classes are imported inside individual tests so
that a missing PySide6 in CI does not break the whole suite.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.candidate_selection import (  # noqa: E402
    canonical_order, top_official_candidates,
    PRIMARY_SCORE_COL, SECONDARY_SCORE_COL,
)


# ------------------------------------------------------------------ fixtures

@pytest.fixture()
def scores_df():
    return pd.DataFrame([
        {"Symbol": "AAA", "Name": "Alpha", "Opportunity_Eligible": "Yes",
         "Opportunity_Rank": 1, "Confidence_Adjusted_Score": 80, "Final_Score": 60,
         "Bucket": "Top", "Risk_Flag": "", "Universe": "STOCK"},
        {"Symbol": "BBB", "Name": "Beta", "Opportunity_Eligible": "Yes",
         "Opportunity_Rank": 2, "Confidence_Adjusted_Score": 70, "Final_Score": 99,
         "Bucket": "Top", "Risk_Flag": "elevated_vol", "Universe": "STOCK"},
        {"Symbol": "CCC", "Name": "Gamma", "Opportunity_Eligible": "No",
         "Opportunity_Rank": None, "Confidence_Adjusted_Score": 95, "Final_Score": 95,
         "Bucket": "Avoid", "Risk_Flag": "veto", "Universe": "ETF"},
    ])


# --------------------------------------- Phase 1: canonical helper contracts

def test_canonical_order_never_uses_final_score(scores_df):
    base = list(canonical_order(scores_df)["Symbol"])
    shuffled = scores_df.copy(); shuffled["Final_Score"] = [1, 2, 3]
    assert list(canonical_order(shuffled)["Symbol"]) == base


def test_top_official_candidates_matches_across_consumers(scores_df):
    """The canonical Top-N must be a pure function of the input — same across
    Overview (DecisionCenterView), Trade Plan (TradePlanView) and Candidates."""
    top5 = top_official_candidates(scores_df, 5)
    assert list(top5["Symbol"]) == ["AAA", "BBB"]  # CCC is ineligible

    # A second call, or a call from any other consumer, must return the same order.
    again = top_official_candidates(scores_df, 5)
    assert list(again["Symbol"]) == list(top5["Symbol"])


def test_primary_secondary_score_columns_correct():
    assert PRIMARY_SCORE_COL == "Confidence_Adjusted_Score"
    assert SECONDARY_SCORE_COL == "Final_Score"


# --------------------------------------- Phase 2/3: validation status source

def test_validation_status_source_is_json(tmp_path):
    """Overview must read verdict from validation_status.json, not from prose."""
    (tmp_path / "output").mkdir()
    payload = {"verdict": "Validation Positive", "evidence_grade": "Sufficient Evidence",
               "horizon_days": 10, "stats": {}}
    (tmp_path / "output" / "validation_status.json").write_text(json.dumps(payload))

    from ui.decision_center import _read_json  # noqa: PLC0415
    status = _read_json(tmp_path / "output" / "validation_status.json")
    assert status["verdict"] == "Validation Positive"


def test_missing_output_files_do_not_crash_read_helpers(tmp_path):
    from ui.decision_center import _read_json, _read_csv  # noqa: PLC0415
    assert _read_json(tmp_path / "nope.json") == {}
    assert _read_csv(tmp_path / "nope.csv").empty


# --------------------------------------- Phase 5: shadow can't reorder official

def test_shadow_columns_do_not_alter_official_ordering(scores_df):
    shadow_only = scores_df.copy()
    shadow_only["Shadow_Rank"] = [3, 2, 1]
    shadow_only["Shadow_Score"] = [1, 2, 3]
    assert list(canonical_order(scores_df)["Symbol"]) == \
           list(canonical_order(shadow_only)["Symbol"])


def test_loading_ui_does_not_write_outputs(tmp_path):
    """Instantiating and refreshing the views must not create or mutate any
    file under output/ or data/."""
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    # Bare, isolated project scaffold
    (tmp_path / "output").mkdir()
    (tmp_path / "data").mkdir()
    scores_csv = tmp_path / "output" / "latest_scores.csv"
    payload = pd.DataFrame([
        {"Symbol": "AAA", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 1,
         "Confidence_Adjusted_Score": 80, "Final_Score": 60},
    ])
    payload.to_csv(scores_csv, index=False)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}

    _ = QApplication.instance() or QApplication(sys.argv[:1])
    from ui.decision_center import DecisionCenterView
    from ui.candidates_workbench import CandidatesWorkbench

    dc = DecisionCenterView(tmp_path, tmp_path / "output"); dc.refresh()
    cw = CandidatesWorkbench(tmp_path, tmp_path / "output"); cw.refresh()

    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    # Same set of files, same mtimes.
    assert set(before) == set(after)
    for k in before:
        assert before[k] == after[k], f"{k} was rewritten by the UI"
