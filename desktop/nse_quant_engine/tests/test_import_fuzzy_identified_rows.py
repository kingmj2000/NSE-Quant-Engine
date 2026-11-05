"""Name-only fuzzy matching must never borrow another scheme's AUM/TER.

2026-11-05 run: the fuzzy fallback in _candidate_import_rows compared names with
AMC words stripped, so "Bajaj Finserv BSE Top 10 Banks ETF" and "DSP BSE Top 10
Banks ETF" looked identical (score 0.75). Every row of the auto AUM/TER imports
names its own symbol and ISIN, so a name-only hit there is by construction a
DIFFERENT product. 20 ETFs got such values, e.g. BANK10BETF, BANKCASE and
EBANK10 all AUM 1,720.42 cr / TER 0.12%, HDFCNIMEG and SBIDEFETF a defence
fund's 1,817.29 cr / 1.51%, IVZSENSEX TER 4.00%, SMALLIETF an index fund's
figures (its confidence 4 -> 9, CAS 14.8 -> 37.5).

overlay_existing_manual then carried them forward from manual_etf_quality.csv,
so they would have stuck. Fix: the fuzzy fallback ignores rows that carry an
identifier, and stored values whose source says "(Import Fuzzy)" are not
carried forward (re-derived each run instead), which also clears the 20.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import etf_metadata_enricher as enr  # noqa: E402

AUM_FILE = "auto_amfi_aum_latest.csv"
TER_FILE = "auto_amfi_ter_tracking_latest.csv"

BANK10BETF = {   # identity as get_etf_identity() + match_nav() leave it
    "Symbol": "BANK10BETF.NS",
    "Raw_Symbol": "BANK10BETF",
    "ISIN": "INF0QA701CI0",
    "AMFI_Scheme_Code": "154492",
    "Name": "BFAM - BANK10BETF",
    "AMFI_Scheme_Name": "Bajaj Finserv BSE Top 10 Banks ETF",
}


def _imp(sym, isin, name, code, aum=np.nan, ter=np.nan, bench=np.nan, src=AUM_FILE):
    return {
        "Import_Symbol": sym, "Import_ISIN": isin, "Import_Name": name,
        "Import_Scheme_Code": code, "AUM_Cr": aum, "TER": ter,
        "Tracking_Error": np.nan, "Tracking_Difference": np.nan,
        "Benchmark_Index": bench, "Source_File": src,
    }


def _real_bank10_import_rows():
    """The rows the 2026-11-05 run matched for BANK10BETF (other AMCs' ETFs)."""
    return pd.DataFrame([
        _imp("ABSL10BANK.NS", "INF209KC1670", "Aditya Birla Sun Life BSE Top 10 Banks ETF",
             "154224.0", aum=1720.42, bench="Other Scheme - Other  ETFs"),
        _imp("BANK10ADD.NS", "INF740KA1YH2", "DSP BSE Top 10 Banks ETF", "154256.0", aum=181.86),
        _imp("ABSL10BANK.NS", "INF209KC1670", "Aditya Birla Sun Life BSE Top 10 Banks ETF",
             np.nan, ter=0.0012, src=TER_FILE),
        _imp("BANK10ADD.NS", "INF740KA1YH2", "DSP BSE Top 10 Banks ETF", np.nan,
             ter=0.0012, src=TER_FILE),
        _imp("MOBANK10.NS", "INF247L01GV8", "Motilal Oswal BSE Top 10 Banks ETF", np.nan,
             ter=0.0012, src=TER_FILE),
    ])


def test_names_alone_would_match_so_the_guard_is_needed():
    # Documents why the old fallback fired: AMC words are dropped before scoring.
    score = enr.combined_score(BANK10BETF["AMFI_Scheme_Name"], "DSP BSE Top 10 Banks ETF")
    assert score >= 0.62


def test_identified_rows_of_other_schemes_are_not_fuzzy_candidates():
    imports = _real_bank10_import_rows()
    assert enr._candidate_import_rows(BANK10BETF, imports) == []
    merged, diags = enr.match_import_metadata(BANK10BETF, imports)
    assert merged == {}
    assert diags and diags[0]["Match_Type"] == "No import match"


def test_exact_identifier_matches_still_work():
    imports = _real_bank10_import_rows()
    own = pd.DataFrame([_imp("BANK10BETF.NS", "INF0QA701CI0",
                             "Bajaj Finserv BSE Top 10 Banks ETF", "154492", aum=95.5)])
    merged, _ = enr.match_import_metadata(BANK10BETF, pd.concat([imports, own], ignore_index=True))
    assert merged["AUM_Cr"] == 95.5
    assert "(Import Symbol)" in merged["AUM_Source"]


def test_name_only_rows_still_get_the_fuzzy_fallback():
    # A hand-made import file with just a scheme-name column: the case the
    # fallback exists for. Blank identifiers arrive as NaN or the text 'nan'.
    imports = pd.concat([
        _real_bank10_import_rows(),
        pd.DataFrame([
            _imp(np.nan, "nan", "Bajaj Finserv BSE Top 10 Banks ETF", np.nan,
                 aum=96.0, ter=0.0015, src="my_manual_import.csv"),
        ]),
    ], ignore_index=True)
    cands = enr._candidate_import_rows(BANK10BETF, imports)
    assert [c[0] for c in cands] == ["Import Fuzzy"]
    hit = cands[0][2]
    assert hit["Source_File"].tolist() == ["my_manual_import.csv"]
    merged, _ = enr.match_import_metadata(BANK10BETF, imports)
    assert merged["AUM_Cr"] == 96.0 and merged["TER"] == 0.0015


def _stored_row(aum_src, ter_src, bench_src):
    """A manual_etf_quality.csv row as the 2026-11-05 run wrote it."""
    return pd.Series({
        "Symbol": "BANK10BETF.NS", "AMFI_Scheme_Code": 154492,
        "AUM_Cr": 1720.42, "AUM_Source": aum_src,
        "TER": 0.0012, "TER_Source": ter_src,
        "Tracking_Error": np.nan, "Tracking_Error_Source": np.nan,
        "Tracking_Difference": np.nan, "Tracking_Difference_Source": np.nan,
        "Benchmark_Index": "Other Scheme - Other  ETFs", "Benchmark_Source": bench_src,
    })


def test_overlay_does_not_carry_forward_fuzzy_sourced_values():
    stored = _stored_row(f"{AUM_FILE} (Import Fuzzy)",
                         f"{TER_FILE} (Import Fuzzy)",
                         f"{AUM_FILE} (Import Fuzzy)")
    fresh = {"Symbol": "BANK10BETF.NS", "AMFI_Scheme_Code": "154492"}   # no import match now
    out = enr.overlay_existing_manual(dict(fresh), stored)
    for field in ("AUM_Cr", "TER", "Benchmark_Index"):
        assert pd.isna(out.get(field, np.nan)), field
    assert enr.metadata_confidence(out) == 0.0


def test_overlay_keeps_identifier_sourced_and_hand_entered_values():
    # Updated for the AMFI parser v2 fix: auto-import values are carried forward
    # only when written by the v2 parser (tagged), and an AMFI category label is
    # never a benchmark (see tests/test_metadata_overlay_fresh_wins.py).
    v2 = enr.V2_SOURCE_TAG
    stored = _stored_row(f"{AUM_FILE} {v2} (Import Symbol)",      # exact match, v2 parser
                         "Existing manual_etf_quality.csv",       # carried / hand-entered
                         np.nan)                                  # no source recorded
    stored["Benchmark_Index"] = "BSE Bankex Top 10 TRI"
    out = enr.overlay_existing_manual({"Symbol": "BANK10BETF.NS"}, stored)
    assert out["AUM_Cr"] == 1720.42
    assert out["TER"] == 0.0012
    assert out["Benchmark_Index"] == "BSE Bankex Top 10 TRI"
    assert out["AUM_Source"].startswith("Carried forward (")


def test_overlay_handles_mixed_sources_per_field():
    v2 = enr.V2_SOURCE_TAG
    stored = _stored_row(f"{AUM_FILE} {v2} (Import Symbol)",
                         f"{TER_FILE} {v2} (Import Fuzzy)",
                         f"{AUM_FILE} {v2} (Import Symbol); {TER_FILE} (Import Fuzzy)")
    stored["Benchmark_Index"] = "BSE Bankex Top 10 TRI"
    out = enr.overlay_existing_manual({"Symbol": "BANK10BETF.NS"}, stored)
    assert out["AUM_Cr"] == 1720.42           # its own symbol matched
    assert pd.isna(out.get("TER", np.nan))    # borrowed from another scheme
    # A field whose source list includes a fuzzy hit is re-derived, not trusted.
    assert pd.isna(out.get("Benchmark_Index", np.nan))


def test_two_run_self_heal_on_the_real_rows():
    """Run N wrote the junk; run N+1 (patched) must end with no borrowed values."""
    imports = _real_bank10_import_rows()
    stored = _stored_row(f"{AUM_FILE} (Import Fuzzy)", f"{TER_FILE} (Import Fuzzy)",
                         f"{AUM_FILE} (Import Fuzzy)")
    row = dict(BANK10BETF)
    meta, _ = enr.match_import_metadata(row, imports)
    row.update(meta)
    row = enr.overlay_existing_manual(row, stored)
    assert pd.isna(row.get("AUM_Cr", np.nan))
    assert pd.isna(row.get("TER", np.nan))
    assert "AUM missing" in enr.metadata_flags(row)
