"""manual_etf_quality.csv carry-forward: fresh imports win; legacy values expire.

2026-11-05 run folder: overlay_existing_manual() overwrote every validated value
from this run's imports with the value stored in manual_etf_quality.csv, so a
corrected AMFI file could never replace a wrong one (NIFTYBEES would keep AUM
0.57 cr forever). It also labelled carried values under f"{field}_Source"
(AUM_Cr_Source / Benchmark_Index_Source), names the output never writes.

Fix: stored values only fill gaps; values that came from an auto_amfi_* import
written before parser v2 (unit/category bugs) or from a name-only fuzzy match are
never carried; carried values are revalidated (TER cap, category-label
benchmarks) and labelled in the real source column. standardize_import_metadata
also ignores pre-v2 AUM files and drops numbers from non-ETF category rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import etf_metadata_enricher as enr  # noqa: E402

AUM_FILE = "auto_amfi_aum_latest.csv"
TER_FILE = "auto_amfi_ter_tracking_latest.csv"
V2 = enr.V2_SOURCE_TAG


def _stored(**kw):
    row = {"Symbol": "NIFTYBEES.NS", "AMFI_Scheme_Code": 140084,
           "AUM_Cr": 0.57, "AUM_Source": f"{AUM_FILE} (Import Symbol); {AUM_FILE} (Import ISIN)",
           "TER": np.nan, "TER_Source": np.nan,
           "Tracking_Error": np.nan, "Tracking_Error_Source": np.nan,
           "Tracking_Difference": -0.0034, "Tracking_Difference_Source": f"{TER_FILE} (Import Symbol)",
           "Benchmark_Index": "Other Scheme - Other  ETFs", "Benchmark_Source": f"{TER_FILE} (Import Symbol)"}
    row.update(kw)
    return pd.Series(row)


def test_fresh_import_value_wins_over_stored():
    fresh = {"Symbol": "NIFTYBEES.NS", "AUM_Cr": 57058.66, "AUM_Source": f"{AUM_FILE} {V2} (Import Symbol)"}
    stored = _stored(AUM_Source=f"Existing manual_etf_quality.csv")
    out = enr.overlay_existing_manual(dict(fresh), stored)
    assert out["AUM_Cr"] == 57058.66
    assert out["AUM_Source"] == fresh["AUM_Source"]


def test_legacy_auto_values_are_not_carried_forward():
    out = enr.overlay_existing_manual({"Symbol": "NIFTYBEES.NS"}, _stored())
    assert pd.isna(out.get("AUM_Cr", np.nan))                 # 0.57 cr from the lakh bug
    assert pd.isna(out.get("Tracking_Difference", np.nan))    # pre-v2 auto file: re-derived
    assert pd.isna(out.get("Benchmark_Index", np.nan))


def test_v2_values_fill_gaps_with_correct_source_column():
    stored = _stored(AUM_Cr=57058.66, AUM_Source=f"{AUM_FILE} {V2} (Import Symbol)",
                     Benchmark_Index="Nifty 50 TRI", Benchmark_Source=f"{TER_FILE} {V2} (Import Symbol)")
    out = enr.overlay_existing_manual({"Symbol": "NIFTYBEES.NS"}, stored)
    assert out["AUM_Cr"] == 57058.66
    assert out["AUM_Source"] == f"Carried forward ({AUM_FILE} {V2} (Import Symbol))"
    assert out["Benchmark_Index"] == "Nifty 50 TRI"
    assert out["Benchmark_Source"].startswith("Carried forward (")
    assert "AUM_Cr_Source" not in out and "Benchmark_Index_Source" not in out
    again = enr.overlay_existing_manual({"Symbol": "NIFTYBEES.NS"}, pd.Series({**stored.to_dict(), **out}))
    assert again["AUM_Source"].count("Carried forward") == 1     # no nesting


def test_carried_values_are_revalidated():
    stored = _stored(AUM_Source="Existing manual_etf_quality.csv", AUM_Cr=np.nan,
                     TER=0.04, TER_Source="Existing manual_etf_quality.csv",
                     Benchmark_Source="Existing manual_etf_quality.csv")
    out = enr.overlay_existing_manual({"Symbol": "IVZSENSEX.NS"}, stored)
    assert pd.isna(out.get("TER", np.nan))                    # 4.00% > SEBI 1% cap
    assert pd.isna(out.get("Benchmark_Index", np.nan))        # a category label


def _raw_imports():
    """Rows as read_import_files() returns them from the June 2026 auto files."""
    aum_legacy = pd.DataFrame([
        {"Symbol": "NIFTYBEES.NS", "NSE Symbol": "NIFTYBEES", "ISIN": "INF204KB14I2",
         "Scheme Name": "Nippon India ETF Nifty 50 BeES", "AUM Cr": 0.57, "Scheme_Code": 140084,
         "__Source_File": AUM_FILE},
    ])
    ter_legacy = pd.DataFrame([
        {"Symbol": "DEFENCE.NS", "NSE Symbol": "DEFENCE", "ISIN": "INF769K01QC2", "AMFI_Scheme_Code": 154182.0,
         "Scheme Name": "Mirae Asset BSE India Defence ETF", "TER": 0.0151,
         "Benchmark Index": "Equity Scheme - Sectoral/ Thematic", "__Source_File": TER_FILE},
        {"Symbol": "ITBEES.NS", "NSE Symbol": "ITBEES", "ISIN": "INF204KB15V2", "AMFI_Scheme_Code": 148408.0,
         "Scheme Name": "Nippon India ETF Nifty IT", "TER": 0.0016,
         "Benchmark Index": "Other Scheme - Other  ETFs", "__Source_File": TER_FILE},
        {"Symbol": "NIFTYBEES.NS", "NSE Symbol": "NIFTYBEES", "ISIN": "INF204KB14I2", "AMFI_Scheme_Code": 140084.0,
         "Scheme Name": "Nippon India ETF Nifty 50 BeES", "Tracking Difference": -0.0034,
         "Benchmark Index": "Nifty 50 TRI", "__Source_File": TER_FILE},
    ])
    aum_v2 = pd.DataFrame([
        {"Symbol": "NIFTYBEES.NS", "NSE Symbol": "NIFTYBEES", "ISIN": "INF204KB14I2",
         "Scheme Name": "Nippon India ETF Nifty 50 BeES", "AUM Cr": 57058.66, "Scheme_Code": 140084,
         "Scheme Category": "Other Scheme - Other ETFs", "Parser_Version": 2, "__Source_File": "auto_amfi_aum_2026_11.csv"},
    ])
    return pd.concat([aum_legacy, ter_legacy, aum_v2], ignore_index=True)


def test_standardize_import_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(enr, "IMPORT_STANDARDIZED_OUT", tmp_path / "std.csv")
    out = enr.standardize_import_metadata(_raw_imports())
    legacy_aum = out[out["Source_File"].eq(AUM_FILE)].iloc[0]
    assert pd.isna(legacy_aum["AUM_Cr"]) and legacy_aum["AUM_Validation"] == "legacy_amfi_aum_parser_units"
    v2_aum = out[out["Source_File"].eq("auto_amfi_aum_2026_11.csv")].iloc[0]
    assert v2_aum["AUM_Cr"] == pytest.approx(57058.66)
    defence = out[out["Import_Symbol"].eq("DEFENCE.NS")].iloc[0]
    assert pd.isna(defence["TER"]) and "non_etf_scheme_category" in defence["TER_Validation"]
    itbees = out[out["Import_Symbol"].eq("ITBEES.NS")].iloc[0]
    assert itbees["TER"] == pytest.approx(0.0016)
    assert pd.isna(itbees["Benchmark_Index"])                  # category label, not a benchmark
    bees_td = out[out["Import_Symbol"].eq("NIFTYBEES.NS") & out["Source_File"].eq(TER_FILE)].iloc[0]
    assert bees_td["Benchmark_Index"] == "Nifty 50 TRI"
    assert bees_td["Tracking_Difference"] == pytest.approx(-0.0034)


def test_v2_rows_are_tagged_in_source():
    r = pd.Series({"Source_File": "auto_amfi_aum_2026_11.csv", "Parser_Version": 2, "AUM_Cr": 57058.66,
                   "Import_Scheme_Code": "140084"})
    md = enr.row_to_metadata(r, "Import Symbol")
    assert V2 in md["AUM_Source"]
    r_legacy = pd.Series({"Source_File": TER_FILE, "Parser_Version": 0, "TER": 0.0016})
    assert V2 not in enr.row_to_metadata(r_legacy, "Import Symbol")["TER_Source"]


def test_sanity_caps():
    assert enr.MAX_REASONABLE_TER_DECIMAL == pytest.approx(0.01)
    assert enr.MAX_REASONABLE_ETF_AUM_CR >= 300000
