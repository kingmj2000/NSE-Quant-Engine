"""Network-free tests for measured source policy and cache safety."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core import optional_data_fetchers as odf  # noqa: E402


def test_retired_sources_are_not_in_default_chain(monkeypatch):
    monkeypatch.delenv("NSE_TRY_ALL_SOURCES", raising=False)
    sources = [("moneycontrol", object()), ("groww", object()),
               ("trendlyne", object()), ("nse-api", object())]
    assert [name for name, _ in odf._filter_known_unavailable("fii_dii", sources)] == ["nse-api"]


def test_explicit_reprobe_can_include_retired_sources(monkeypatch):
    monkeypatch.setenv("NSE_TRY_ALL_SOURCES", "1")
    sources = [("moneycontrol", object()), ("nse-api", object())]
    assert [name for name, _ in odf._filter_known_unavailable("fii_dii", sources)] == [
        "moneycontrol", "nse-api"
    ]


def test_guarded_cache_treats_missing_empty_and_corrupt_as_no_cache(tmp_path):
    missing = tmp_path / "missing.csv"
    empty = tmp_path / "empty.csv"
    corrupt = tmp_path / "corrupt.csv"
    empty.touch()
    corrupt.write_text("not,a,valid\n\"unterminated\n", encoding="utf-8")
    assert odf._read_csv_guarded(missing, "test") is None
    assert odf._read_csv_guarded(empty, "test") is None
    assert odf._read_csv_guarded(corrupt, "test") is None


def test_missing_date_ranges_has_exact_contract_and_no_network(tmp_path):
    target = tmp_path / "flows.csv"
    target.write_text("Date,FII_Net_INR_Cr,DII_Net_INR_Cr\n", encoding="utf-8")
    ranges = odf.missing_date_ranges(target, 5)
    assert isinstance(ranges, list)
    assert ranges
    assert all(len(item) == 2 for item in ranges)


def test_fundamentals_retains_cached_symbol_inside_universe_outside_shortlist(monkeypatch, tmp_path):
    """The full config universe, not today's capped shortlist, controls pruning."""
    (tmp_path / "output").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "config.csv").write_text(
        "Symbol,Include\nSHORT.NS,Yes\nOUTSIDE.NS,Yes\n", encoding="utf-8"
    )
    (tmp_path / "output" / "latest_scores.csv").write_text(
        "Symbol\nSHORT.NS\n", encoding="utf-8"
    )
    pd.DataFrame([{
        "Symbol": "OUTSIDE.NS", "Fundamental_Score": 77.0,
        "Fundamental_Coverage": 1.0, "As_Of": "2026-08-01",
    }]).to_csv(tmp_path / "data" / "fundamentals_latest.csv", index=False)
    old = time.time() - 3600 * 24 * 14
    os.utime(tmp_path / "data" / "fundamentals_latest.csv", (old, old))

    monkeypatch.setattr("core.fundamental_factor.fetch_fundamentals", lambda syms, sleep=0: pd.DataFrame([
        {"Symbol": "SHORT.NS", "PE": 10.0, "ROE": 0.2, "DebtToEquity": 0.2,
         "EarningsGrowth": 0.1, "ProfitMargin": 0.15}
    ]))
    ok = odf.fetch_fundamentals(tmp_path / "data", tmp_path, cap=1, force=True)
    assert ok
    output = pd.read_csv(tmp_path / "data" / "fundamentals_latest.csv")
    retained = output.loc[output["Symbol"] == "OUTSIDE.NS", "Fundamental_Score"]
    assert len(retained) == 1
    assert retained.iloc[0] == 77.0


def test_fundamentals_drops_cached_symbol_removed_from_full_universe(monkeypatch, tmp_path):
    (tmp_path / "output").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "config.csv").write_text("Symbol,Include\nSHORT.NS,Yes\n", encoding="utf-8")
    (tmp_path / "output" / "latest_scores.csv").write_text("Symbol\nSHORT.NS\n", encoding="utf-8")
    pd.DataFrame([{
        "Symbol": "REMOVED.NS", "Fundamental_Score": 77.0,
        "Fundamental_Coverage": 1.0, "As_Of": "2026-08-01",
    }]).to_csv(tmp_path / "data" / "fundamentals_latest.csv", index=False)
    monkeypatch.setattr("core.fundamental_factor.fetch_fundamentals", lambda syms, sleep=0: pd.DataFrame())
    assert odf.fetch_fundamentals(tmp_path / "data", tmp_path, cap=1, force=True)
    output = pd.read_csv(tmp_path / "data" / "fundamentals_latest.csv")
    assert "REMOVED.NS" not in set(output["Symbol"])
