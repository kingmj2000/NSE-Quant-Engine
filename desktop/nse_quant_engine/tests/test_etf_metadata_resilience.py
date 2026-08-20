"""ETF metadata resilience: an unreachable AMFI must not break the pipeline.

Two compounding defects, both instances of this codebase's recurring shape —
"could not be done" recorded as "was done":

1. `etf_metadata_enricher.fetch_amfi_navall()` treated a parse yielding zero rows
   as SUCCESS and wrote the empty frame over `amfi_navall_latest.csv`. That
   destroyed a working 14,280-row cache during a transient DNS outage.

2. `etf_quality_builder.make_mapping_suggestions()` then called
   `candidates.iloc[0]` on the now-empty NAV table and raised IndexError, which
   HALTED the entire pipeline — an optional metadata enrichment source taking
   down scoring, validation and everything downstream.

Together they converted a temporary network failure into a permanently broken
run: even after DNS recovered, the poisoned cache kept the crash alive.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ENG = Path(__file__).resolve().parents[1]


def _load(mod_name: str, filename: str):
    if str(ENG) not in sys.path:
        sys.path.insert(0, str(ENG))
    spec = importlib.util.spec_from_file_location(mod_name, ENG / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _etfs() -> pd.DataFrame:
    return pd.DataFrame({
        "Name": ["Nippon India ETF Nifty BeES", "SBI Gold ETF"],
        "Symbol": ["NIFTYBEES.NS", "SETFGOLD.NS"],
        "ISIN": ["INF204KB14I2", ""],
        "Raw_Symbol": ["NIFTYBEES", "SETFGOLD"],
        "Category": ["ETF", "ETF"],
    })


NAV_COLS = ["AMFI_Scheme_Code", "AMFI_Scheme_Name",
            "AMFI_ISIN_Growth", "AMFI_ISIN_Div", "NAV", "NAV_Date"]


# ── 1. the pipeline-halting IndexError ──────────────────────────────────────

@pytest.mark.parametrize("nav", [
    pd.DataFrame(),                       # AMFI never answered
    pd.DataFrame(columns=NAV_COLS),       # answered with no rows
])
def test_empty_nav_table_does_not_halt_the_pipeline(nav):
    eqb = _load("eqb_empty", "etf_quality_builder.py")
    out = eqb.make_mapping_suggestions(_etfs(), nav)

    assert len(out) == 2, "every ETF must still get a row"
    assert set(out["Mapping_Status"]) == {"Missing"}
    assert all(str(v) == "" for v in out["Suggested_AMFI_Scheme_Code"])


def test_no_match_is_reported_as_missing_not_verified():
    """A blank suggestion must never look like a confident mapping."""
    eqb = _load("eqb_status", "etf_quality_builder.py")
    assert eqb.mapping_status("Unavailable", 0.0) == "Missing"
    assert eqb.mapping_status("ISIN", 1.0) == "Verified"


def test_populated_nav_table_still_matches():
    """Degrading gracefully must not degrade the working path."""
    eqb = _load("eqb_ok", "etf_quality_builder.py")
    nav = pd.DataFrame([{
        "AMFI_Scheme_Code": "101234",
        "AMFI_Scheme_Name": "Nippon India ETF Nifty BeES",
        "AMFI_ISIN_Growth": "INF204KB14I2",
        "AMFI_ISIN_Div": "",
        "NAV": 250.5,
        "NAV_Date": "20-Aug-2026",
    }])
    out = eqb.make_mapping_suggestions(_etfs(), nav)

    row = out[out["Symbol"] == "NIFTYBEES.NS"].iloc[0]
    assert row["Mapping_Status"] == "Verified"
    assert row["Suggested_AMFI_Scheme_Code"] == "101234"


# ── 2. the destroyed NAV cache ──────────────────────────────────────────────

def test_degraded_fetch_never_overwrites_a_good_cache(tmp_path, monkeypatch):
    enr = _load("enr_cache", "etf_metadata_enricher.py")
    cache = tmp_path / "amfi_navall_latest.csv"
    pd.DataFrame({"Scheme_Code": range(14280), "NAV": [1.0] * 14280}).to_csv(
        cache, index=False)
    monkeypatch.setattr(enr, "AMFI_NAV_OUT", cache)

    # A response with a handful of rows is degraded, not fresh truth.
    enr._write_nav_cache_if_better(pd.DataFrame({"Scheme_Code": [1], "NAV": [1.0]}), [])

    assert len(pd.read_csv(cache)) == 14280, "the good cache was overwritten"


def test_a_full_fetch_does_replace_the_cache(tmp_path, monkeypatch):
    enr = _load("enr_write", "etf_metadata_enricher.py")
    cache = tmp_path / "amfi_navall_latest.csv"
    pd.DataFrame({"Scheme_Code": range(100), "NAV": [1.0] * 100}).to_csv(
        cache, index=False)
    monkeypatch.setattr(enr, "AMFI_NAV_OUT", cache)

    fresh = pd.DataFrame({"Scheme_Code": range(14280), "NAV": [2.0] * 14280})
    enr._write_nav_cache_if_better(fresh, [])

    assert len(pd.read_csv(cache)) == 14280


def test_cache_is_written_when_none_exists(tmp_path, monkeypatch):
    enr = _load("enr_new", "etf_metadata_enricher.py")
    cache = tmp_path / "amfi_navall_latest.csv"
    monkeypatch.setattr(enr, "AMFI_NAV_OUT", cache)

    enr._write_nav_cache_if_better(pd.DataFrame({"Scheme_Code": [1], "NAV": [1.0]}), [])
    assert cache.exists() and len(pd.read_csv(cache)) == 1


def test_cache_loader_tolerates_a_corrupt_file(tmp_path, monkeypatch):
    enr = _load("enr_bad", "etf_metadata_enricher.py")
    cache = tmp_path / "amfi_navall_latest.csv"
    cache.write_bytes(b"\x00\xff not a csv")
    monkeypatch.setattr(enr, "AMFI_NAV_OUT", cache)

    assert enr._load_nav_cache().empty


def test_zero_parsed_rows_is_not_treated_as_success():
    """The write path must be guarded by an emptiness check."""
    src = (ENG / "etf_metadata_enricher.py").read_text(encoding="utf-8")
    i = src.index("def fetch_amfi_navall")
    body = src[i:src.index("def _load_nav_cache")]
    assert "if df.empty:" in body, "an empty parse must not be recorded as OK"
    assert "_write_nav_cache_if_better" in body, "cache write must be guarded"
    assert "df.to_csv(AMFI_NAV_OUT" not in body, "unconditional cache write is back"
