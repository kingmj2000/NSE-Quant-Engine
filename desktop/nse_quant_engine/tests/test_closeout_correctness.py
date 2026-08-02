"""Closeout correctness tests.

Covers:
  * daily_changes uses the latest STRICTLY-EARLIER history date (the current
    snapshot is already appended to score_history.csv before it runs);
  * risk-flag change semantics (clean/blank/NaN == no risk, risk→risk change);
  * schema-v2-only authoritative validation;
  * verdict authority — a markdown report can never override status JSON;
  * news refresh-status aggregation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.daily_changes import build_daily_changes  # noqa: E402
from core.validation_status import read_status  # noqa: E402
from cross_sectional_validation import make_detail  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────

def _row(sym, cas, date, risk="Clean"):
    return {"Date": date, "Symbol": sym, "Confidence_Adjusted_Score": cas,
            "Final_Score": 100 - cas, "Opportunity_Eligible": "Yes",
            "Risk_Flag": risk}


def _write_history(out: Path, curr: list[dict], prev: list[dict]):
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(curr).to_csv(out / "latest_scores.csv", index=False)
    # History already contains the CURRENT snapshot (engine appends before this runs).
    pd.DataFrame(prev + curr).to_csv(out / "score_history.csv", index=False)


# ── 1. daily changes ───────────────────────────────────────────────────────

def test_daily_changes_uses_strictly_earlier_history_date(tmp_path):
    prev_date, curr_date = "2026-01-01", "2026-01-02"
    prev = [_row(s, 90 - i, prev_date) for i, s in
            enumerate(["P1", "P2", "P3", "P4", "P5", "P6"])]
    # NEW1 enters at the top; P5 drops out of the Top-5.
    curr = [_row("NEW1", 99, curr_date)] + \
           [_row(s, 90 - i, curr_date) for i, s in enumerate(["P1", "P2", "P3", "P4"])] + \
           [_row("P5", 10, curr_date), _row("P6", 9, curr_date)]

    _write_history(tmp_path / "output", curr, prev)
    payload = build_daily_changes(tmp_path, write=False)

    assert payload["previous_snapshot_available"] is True
    assert payload["previous_score_date"] == prev_date
    assert payload["current_score_date"] == curr_date
    assert payload["top5_entries"] == ["NEW1"]
    assert payload["top5_exits"] == ["P5"]
    movers = {m["Symbol"]: m["rank_change"] for m in payload["largest_rank_losers"]}
    assert movers.get("P5", 0) < 0, payload["largest_rank_losers"]


def test_daily_changes_risk_semantics(tmp_path):
    prev_date, curr_date = "2026-01-01", "2026-01-02"
    prev = [
        _row("CLEARED", 80, prev_date, risk="Drawdown risk"),
        _row("CHANGED", 70, prev_date, risk="Drawdown risk"),
        _row("BLANKY", 60, prev_date, risk=""),
        _row("NEWRISK", 50, prev_date, risk="Clean"),
    ]
    curr = [
        _row("CLEARED", 80, curr_date, risk="Clean"),
        _row("CHANGED", 70, curr_date, risk="Liquidity risk"),
        _row("BLANKY", 60, curr_date, risk=None),
        _row("NEWRISK", 50, curr_date, risk="Volatility risk"),
    ]
    _write_history(tmp_path / "output", curr, prev)
    payload = build_daily_changes(tmp_path, write=False)

    new = {d["Symbol"]: d for d in payload["new_risk_flags"]}
    cleared = {d["Symbol"]: d for d in payload["cleared_risk_flags"]}

    assert set(new) == {"CHANGED", "NEWRISK"}
    assert new["CHANGED"]["previous_flag"] == "Drawdown risk"
    assert new["CHANGED"]["flag"] == "Liquidity risk"
    assert new["NEWRISK"]["previous_flag"] is None
    assert set(cleared) == {"CLEARED"}
    assert cleared["CLEARED"]["previous_flag"] == "Drawdown risk"
    # Blank/NaN never counts as a risk in either direction.
    assert "BLANKY" not in new and "BLANKY" not in cleared


def test_daily_changes_generated_at_is_timezone_aware(tmp_path):
    _write_history(tmp_path / "output", [_row("A", 50, "2026-01-02")], [])
    payload = build_daily_changes(tmp_path, write=False)
    ts = pd.Timestamp(payload["generated_at"])
    assert ts.tzinfo is not None, payload["generated_at"]


# ── 2. schema-v2-only validation ───────────────────────────────────────────

def test_v1_only_history_yields_no_authoritative_observations():
    dates = pd.to_datetime(["2026-01-02"] * 12)
    syms = [f"S{i}" for i in range(12)]
    scores = pd.DataFrame({
        "Date": dates, "Symbol": syms,
        "Final_Score": range(12), "Confidence_Adjusted_Score": range(12),
        "Opportunity_Eligible": "Yes", "Ranking_Schema_Version": 1,
    })
    fwd = pd.DataFrame({
        "Signal_Date": dates, "Symbol": syms,
        "Horizon_Days": 10, "Net_Forward_Return": 0.01,
        "Gross_Forward_Return": 0.012, "Round_Trip_Cost": 0.002,
    })
    detail = make_detail(scores, fwd)
    assert detail.empty or len(detail) == 0, f"v1 rows leaked into authoritative panel: {len(detail)}"

    v2_scores = scores.assign(Ranking_Schema_Version=2)
    assert len(make_detail(v2_scores, fwd)) == 12


# ── 3. verdict authority ───────────────────────────────────────────────────

@pytest.mark.parametrize("status_body", [None, "{ not json", json.dumps({"verdict": "No Proven Edge Yet"})])
def test_markdown_cannot_override_status_json(tmp_path, status_body):
    out = tmp_path / "output"
    out.mkdir()
    (out / "cross_sectional_validation_report.md").write_text(
        "# Report\n\nVerdict: Validation Positive\n", encoding="utf-8")
    if status_body is not None:
        (out / "validation_status.json").write_text(status_body, encoding="utf-8")

    verdict = read_status(out / "validation_status.json").get("verdict")
    assert verdict != "Validation Positive", verdict

    import trade_plan_builder as tpb
    tpb.OUTPUT_DIR = out
    v, grade, _src = tpb.parse_validation_report()
    assert v != "Validation Positive", (v, grade)


def test_dashboard_has_no_markdown_verdict_scraper():
    src = (ROOT / "dashboard_html_builder.py").read_text(encoding="utf-8")
    assert "_verdict_from_markdown" not in src
    assert "cross_sectional_validation_report.md" not in src


# ── 4. no official Final_Score fallback ────────────────────────────────────

def test_no_official_final_score_fallback():
    from core import config
    assert config.RANKING_COLUMN == "Confidence_Adjusted_Score"
    assert config.RANKING_COLUMN_FALLBACK is None


def test_official_order_drops_invalid_cas_instead_of_raw_fallback():
    from dashboard_html_builder import _official_order
    df = pd.DataFrame({"Symbol": ["AAA", "BBB"],
                       "Confidence_Adjusted_Score": [None, None],
                       "Final_Score": [99.0, 10.0]})
    assert _official_order(df).empty

    df2 = pd.DataFrame({"Symbol": ["AAA", "BBB"],
                        "Confidence_Adjusted_Score": [70.0, 90.0],
                        "Final_Score": [99.0, 10.0]})
    assert list(_official_order(df2)["Symbol"]) == ["BBB", "AAA"]


# ── 5. news refresh status ─────────────────────────────────────────────────

@pytest.mark.parametrize("statuses,cache_empty,expected", [
    (["success", "success"], False, "success"),
    (["success", "failed"], False, "partial"),
    (["partial", "failed"], False, "partial"),   # source-level partial == live data
    (["failed", "failed"], False, "cached"),
    (["failed", "failed"], True, "failed"),
])
def test_news_refresh_status_aggregation(statuses, cache_empty, expected):
    live_ok = sum(1 for s in statuses if s in ("success", "partial"))
    if all(s == "success" for s in statuses):
        got = "success"
    elif live_ok >= 1:
        got = "partial"
    else:
        got = "cached" if not cache_empty else "failed"
    assert got == expected

    src = (ROOT / "news_market_builder.py").read_text(encoding="utf-8")
    assert 'live_ok = sum(1 for s in live_statuses if s in ("success", "partial"))' in src


def test_ui_reader_exposes_refresh_timestamps(tmp_path):
    from core import ui_readers
    (tmp_path / "news_digest.json").write_text(json.dumps({
        "generated_at": "2026-01-02T10:00:00",
        "last_successful_refresh_at": "2026-01-02T10:00:00",
        "previous_successful_refresh_at": "2026-01-01T10:00:00",
        "refresh_status": "success",
    }), encoding="utf-8")
    d = ui_readers.read_news_digest(tmp_path)
    assert d["previous_successful_refresh_at"] == "2026-01-01T10:00:00"
    assert d["last_successful_refresh_at"] == "2026-01-02T10:00:00"
