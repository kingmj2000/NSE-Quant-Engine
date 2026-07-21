"""Headless tests for `core.ui_readers` and `core.daily_changes`.

These tests deliberately do NOT import PySide6, so they run in CI without a
display server. They guarantee that:

    * every reader handles a missing artifact without raising
    * every reader normalizes to the shape the UI expects
    * `daily_changes` produces the diff shape the Decision Center consumes
    * shadow_summary derives `champion` from the recommendation string
    * `read_data_health` correctly enumerates red / amber feeds (schema
      is `{generated_at, feeds: {name: {status, ...}}}` — NOT flat).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.ui_readers import (  # noqa: E402
    read_validation_status, read_rebalance_diff, read_daily_changes,
    read_data_health, read_shadow_summary, read_news_digest,
    stories_for_symbol, pick_column, COLUMN_ALIASES,
)
from core.daily_changes import build_daily_changes  # noqa: E402


# ─── missing files never raise ──────────────────────────────────────────────

def test_all_readers_tolerate_missing_files(tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "data").mkdir()
    assert read_validation_status(tmp_path / "output")["empty"] is True
    assert read_rebalance_diff(tmp_path / "output")["empty"] is True
    assert read_daily_changes(tmp_path / "output")["empty"] is True
    assert read_shadow_summary(tmp_path / "output")["empty"] is True
    assert read_news_digest(tmp_path / "output")["empty"] is True
    h = read_data_health(tmp_path)
    assert h["empty"] is True and h["reds"] == [] and h["ambers"] == []


# ─── validation status normalization ────────────────────────────────────────

def test_validation_status_normalizes_schema(tmp_path):
    out = tmp_path / "output"; out.mkdir()
    (out / "validation_status.json").write_text(json.dumps({
        "verdict": "Validation Positive", "evidence_grade": "Sufficient Evidence",
        "horizon_days": 10, "stats": {"validation_dates": 42},
        "ranking_column": "Confidence_Adjusted_Score",
        "ranking_schema_version": 2,
    }))
    s = read_validation_status(out)
    assert s["is_valid_positive"] is True
    assert s["ranking_schema_version"] == 2
    assert s["stats"]["validation_dates"] == 42


# ─── shadow summary champion derivation from actual keys ────────────────────

def test_shadow_summary_champion_from_recommendation(tmp_path):
    out = tmp_path / "output"; out.mkdir()
    payload = {
        "jaccard_top25": 0.6, "spearman_full": 0.7,
        "verdict_official": "Validation Positive",
        "verdict_shadow": "Validation Positive",
        "ev_per_day_official": 0.10, "ev_per_day_shadow": 0.09,
        "recommendation": "RECOMMEND: official still leads on EV/day — keep current champion",
    }
    (out / "shadow_vs_official.json").write_text(json.dumps(payload))
    s = read_shadow_summary(out)
    assert s["champion"] == "official"

    payload["recommendation"] = "RECOMMEND: shadow leads on filtered EV/day — consider manual switch"
    (out / "shadow_vs_official.json").write_text(json.dumps(payload))
    assert read_shadow_summary(out)["champion"] == "shadow"


# ─── data_health enumerates nested feeds dict, ignores metadata keys ────────

def test_data_health_reads_nested_feeds_dict(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    (data / "data_health.json").write_text(json.dumps({
        "generated_at": "2026-07-10T06:25:46",
        "feeds": {
            "delivery_pct": {"status": "green"},
            "iv_rank":      {"status": "red", "note": "blocked"},
            "fundamentals": {"status": "amber"},
        },
    }))
    h = read_data_health(tmp_path)
    assert h["reds"] == ["iv_rank"]
    assert h["ambers"] == ["fundamentals"]
    # 'generated_at' at the top level must NOT be mistaken for a feed.
    assert "generated_at" not in h["reds"] + h["ambers"]


# ─── rebalance_diff surfaces flat schema ───────────────────────────────────

def test_rebalance_diff_flat_schema(tmp_path):
    out = tmp_path / "output"; out.mkdir()
    (out / "rebalance_diff.json").write_text(json.dumps({
        "as_of": "2026-07-10",
        "holds": ["AAA", "BBB"], "exits": ["ZZZ"], "entries": ["CCC"],
        "exit_reasons": {"ZZZ": "risk_flag"},
        "estimated_turnover_%": 25.0, "net_edge_after_cost_%": 0.4,
        "recommendation": "Rebalance",
    }))
    r = read_rebalance_diff(out)
    assert r["entries"] == ["CCC"] and r["exits"] == ["ZZZ"]
    assert r["estimated_turnover_pct"] == 25.0
    assert r["recommendation"] == "Rebalance"


# ─── news digest and symbol filter ─────────────────────────────────────────

def test_news_digest_and_symbol_stories(tmp_path):
    out = tmp_path / "output"; out.mkdir()
    (out / "news_digest.json").write_text(json.dumps({
        "schema_version": "nse_news_digest_v1",
        "generated_at": "2026-07-10",
        "refresh_status": "ok",
        "counts": {"candidate_stories": 2},
        "stories": [
            {"Symbol": "AAA", "Canonical_Title": "AAA reports Q1"},
            {"Symbol": "bbb", "Canonical_Title": "BBB updates"},
        ],
    }))
    d = read_news_digest(out)
    assert d["refresh_status"] == "ok"
    assert len(stories_for_symbol(d, "aaa")) == 1
    assert len(stories_for_symbol(d, "BBB")) == 1
    assert stories_for_symbol({}, "AAA") == []


# ─── column alias resolution matches production names ──────────────────────

def test_column_aliases_resolve_production_names():
    df = pd.DataFrame(columns=["Symbol", "RSI_14", "Volatility_20D",
                               "Current_Drawdown_60D"])
    assert pick_column(df, "RSI") == "RSI_14"
    assert pick_column(df, "Volatility") == "Volatility_20D"
    assert pick_column(df, "Drawdown") == "Current_Drawdown_60D"
    assert pick_column(df, "News_Count") is None
    # No hidden aliases that would silently pick a wrong column.
    assert "RSI_14" in COLUMN_ALIASES["RSI"]


# ─── daily_changes builder produces expected shape ─────────────────────────

def test_build_daily_changes_diff_and_movers(tmp_path):
    out = tmp_path / "output"; out.mkdir()

    # Previous snapshot: AAA top, BBB second, CCC third.
    hist = pd.DataFrame([
        {"Date": "2026-07-09", "Symbol": "AAA",
         "Opportunity_Eligible": "Yes", "Opportunity_Rank": 1,
         "Confidence_Adjusted_Score": 90, "Risk_Flag": ""},
        {"Date": "2026-07-09", "Symbol": "BBB",
         "Opportunity_Eligible": "Yes", "Opportunity_Rank": 2,
         "Confidence_Adjusted_Score": 80, "Risk_Flag": ""},
        {"Date": "2026-07-09", "Symbol": "CCC",
         "Opportunity_Eligible": "Yes", "Opportunity_Rank": 3,
         "Confidence_Adjusted_Score": 70, "Risk_Flag": ""},
    ])
    hist.to_csv(out / "score_history.csv", index=False)

    # Current: BBB overtakes AAA, DDD is a new Top-5 entrant, CCC gains a flag.
    curr = pd.DataFrame([
        {"Symbol": "BBB", "Opportunity_Eligible": "Yes",
         "Opportunity_Rank": 1, "Confidence_Adjusted_Score": 95, "Risk_Flag": ""},
        {"Symbol": "AAA", "Opportunity_Eligible": "Yes",
         "Opportunity_Rank": 2, "Confidence_Adjusted_Score": 90, "Risk_Flag": ""},
        {"Symbol": "DDD", "Opportunity_Eligible": "Yes",
         "Opportunity_Rank": 3, "Confidence_Adjusted_Score": 88, "Risk_Flag": ""},
        {"Symbol": "CCC", "Opportunity_Eligible": "Yes",
         "Opportunity_Rank": 4, "Confidence_Adjusted_Score": 60,
         "Risk_Flag": "elevated_vol"},
    ])
    curr.to_csv(out / "latest_scores.csv", index=False)

    payload = build_daily_changes(tmp_path)

    assert payload["schema_version"] == "nse_daily_changes_v1"
    assert payload["ranking_column"] == "Confidence_Adjusted_Score"
    assert "DDD" in payload["top5_entries"]
    assert "DDD" in payload["top20_entries"]
    # CCC gained a risk flag between runs.
    assert any(f["Symbol"] == "CCC" for f in payload["new_risk_flags"])
    # BBB moved from rank 2 to rank 1 → rank_change = +1.
    gainers = {r["Symbol"]: r["rank_change"] for r in payload["largest_rank_gainers"]}
    assert gainers.get("BBB") == 1
    # AAA moved from 1 to 2 → -1.
    losers = {r["Symbol"]: r["rank_change"] for r in payload["largest_rank_losers"]}
    assert losers.get("AAA") == -1

    # File was written and re-readable via read_daily_changes.
    round_trip = read_daily_changes(out)
    assert round_trip["schema_version"] == "nse_daily_changes_v1"


def test_build_daily_changes_no_prior_snapshot(tmp_path):
    out = tmp_path / "output"; out.mkdir()
    pd.DataFrame([
        {"Symbol": "AAA", "Opportunity_Eligible": "Yes",
         "Opportunity_Rank": 1, "Confidence_Adjusted_Score": 90, "Risk_Flag": ""},
    ]).to_csv(out / "latest_scores.csv", index=False)
    payload = build_daily_changes(tmp_path)
    assert payload["previous_snapshot_available"] is False
    assert payload["top5_entries"] == [] and payload["top5_exits"] == []
