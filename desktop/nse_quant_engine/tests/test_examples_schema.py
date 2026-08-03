"""Synthetic examples must match the real reader/writer contracts.

examples/sample_output/ documents artifact SHAPES for the public repository.
If a production schema changes and the samples do not, the samples become
misleading — this test keeps them honest. Data stays fabricated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ui_readers  # noqa: E402
from core import validation_status as vs  # noqa: E402
from core.daily_changes import SCHEMA_VERSION as DC_SCHEMA  # noqa: E402

EXAMPLES = ROOT.parent.parent / "examples" / "sample_output"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_examples_are_labelled_synthetic():
    for name in ["daily_changes.json", "news_digest.json", "validation_status.json"]:
        assert "SYNTHETIC" in _load(name).get("_note", "").upper(), name


def test_daily_changes_matches_reader_contract(tmp_path):
    data = _load("daily_changes.json")
    assert data["schema_version"] == DC_SCHEMA
    assert data["ranking_column"] == "Confidence_Adjusted_Score"
    (tmp_path / "daily_changes.json").write_text(json.dumps(data), encoding="utf-8")
    read = ui_readers.read_daily_changes(tmp_path)
    assert not read["empty"]
    for key in ["top5_entries", "top5_exits", "top20_entries", "top20_exits",
                "largest_rank_gainers", "largest_rank_losers",
                "new_risk_flags", "cleared_risk_flags"]:
        assert key in data, key
        assert read[key] == data[key]
    for mover in data["largest_rank_gainers"] + data["largest_rank_losers"]:
        assert set(mover) == {"Symbol", "previous_rank", "current_rank", "rank_change"}
    for flag in data["new_risk_flags"] + data["cleared_risk_flags"]:
        assert set(flag) == {"Symbol", "flag", "previous_flag"}


def test_news_digest_matches_reader_contract(tmp_path):
    data = _load("news_digest.json")
    (tmp_path / "news_digest.json").write_text(json.dumps(data), encoding="utf-8")
    read = ui_readers.read_news_digest(tmp_path)
    assert not read["empty"]
    assert "items" not in data          # production key is `stories`
    assert read["stories"] == data["stories"] and read["stories"]
    assert read["refresh_status"] in {"success", "partial", "cached", "failed"}
    assert read["last_successful_refresh_at"] and read["previous_successful_refresh_at"]
    required_health = {"Source", "Fetch_Status", "Last_Attempt", "Last_Success",
                       "Items_Received", "Items_Retained", "Unknown_Date_Count",
                       "Duplicate_Count", "Cache_Fallback_Used", "Error"}
    for entry in data["source_health"]:
        assert required_health.issubset(entry), entry


def test_validation_status_matches_writer_contract(tmp_path):
    data = _load("validation_status.json")
    p = tmp_path / "validation_status.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    read = vs.read_status(p)
    assert read["verdict"] == data["verdict"] in vs.VALID_VERDICTS
    written = vs.write_status(tmp_path / "written.json", data["verdict"],
                              data["evidence_grade"], data["stats"],
                              horizon=data["horizon_days"])
    assert set(written).issubset(set(data)), set(written) - set(data)
    assert data["ranking_column"] == "Confidence_Adjusted_Score"
    assert data["ranking_schema_version"] == 2


def test_latest_scores_example_uses_production_columns():
    df = pd.read_csv(EXAMPLES / "latest_scores.csv")
    assert "Raw_Score" not in df.columns
    for col in ["Symbol", "Final_Score", "Confidence_Adjusted_Score",
                "Opportunity_Eligible", "Opportunity_Rank", "Rank",
                "Raw_Score_Rank", "Raw_Score_Bucket", "Risk_Flag"]:
        assert col in df.columns, col
