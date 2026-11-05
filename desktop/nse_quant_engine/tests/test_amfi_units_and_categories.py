"""AMFI TER/AUM units and scheme categories (parser v2).

Measured on the 2026-11-05 run folder (AMFI data fetched June 2026):
* TER: AMFI publishes TER as a percent string ("0.04" = 0.04%). The fetcher kept
  anything <= 0.05 "as a decimal", so 26 Nifty 50 / Sensex ETFs got 4.00%.
* TER: the fuzzy name fallback matched ETFs to index funds (69), FoFs (6), active
  equity (8, e.g. DEFENCE 1.51%) and a liquid fund; their TERs are not ETF TERs.
  SEBI Reg. 52(6) caps ETF TER at 1.00%.
* Benchmark: 311/332 stored "benchmarks" were AMFI category labels.
* AUM: AMFI scheme-wise AAUM is in Rs lakh. The fetcher treated it as crore and
  divided values > 1e6 by 1e7: NIFTYBEES 5,705,866.48 lakh (57,059 cr) -> 0.57 cr;
  small ETFs were inflated 100x. The 1 lakh cr sanity cap rejected SBI Nifty 50
  ETF (210,783 cr).
"""
from __future__ import annotations

import sys
from datetime import date as real_date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import etf_aum_auto_fetcher as aumf  # noqa: E402
import etf_metadata_enricher as enr  # noqa: E402
import etf_ter_tracking_auto_fetcher as terf  # noqa: E402
from core import etf_source_rules as rules  # noqa: E402


# ---------- shared rules ----------------------------------------------------

@pytest.mark.parametrize("label,non_etf", [
    ("Other Scheme - Other  ETFs", False),
    ("Other Scheme - Gold ETF", False),
    ("Other Scheme - Index Funds", True),
    ("Other Scheme - FoF Domestic", True),
    ("Equity Scheme - Sectoral/ Thematic", True),
    ("Debt Scheme - Liquid Fund", True),
])
def test_category_labels(label, non_etf):
    assert rules.is_amfi_category_label(label)
    assert rules.is_non_etf_category(label) is non_etf
    assert rules.benchmark_or_blank(label) == ""


def test_real_benchmarks_are_not_category_labels():
    for bench in ["Nifty 50 TRI", "NIFTY 1D RATE INDEX", "Nifty Bank TRI", "BSE Sensex TRI"]:
        assert not rules.is_amfi_category_label(bench)
        assert rules.benchmark_or_blank(bench) == bench
    assert not rules.is_non_etf_category("")          # unknown is not evidence


# ---------- TER fetcher ------------------------------------------------------

def _amfi_ter_rows():
    """Shape of AMFI /api/populate-te-rdata-revised rows (daily, percent strings)."""
    base = {"SchemeType_Desc": "Open Ended", "TER_Year": "2026-2027", "R_BER": "0.0300",
            "R_BrokerageCost": "0.0000", "R_StatutoryLevies": "0.0100", "D_TER": "", "MF_ID": 25,
            "Month": "10-2026"}
    return pd.DataFrame([
        {**base, "NSDLSchemeCode": "NIPP/O/O/EET/01/12/0001", "Scheme_Name": "Nippon India ETF Nifty 50 BeES",
         "SchemeCat_Desc": "Other Scheme - Other  ETFs", "TER_Date": "2026-10-01T00:00:00.000Z", "R_TER": "0.0400"},
        {**base, "NSDLSchemeCode": "NIPP/O/O/EET/01/12/0001", "Scheme_Name": "Nippon India ETF Nifty 50 BeES",
         "SchemeCat_Desc": "Other Scheme - Other  ETFs", "TER_Date": "2026-10-20T00:00:00.000Z", "R_TER": "0.0500"},
        {**base, "NSDLSchemeCode": "NIPP/O/O/IXF/20/01/0002", "Scheme_Name": "Nippon India Index Fund - Nifty 50 Plan",
         "SchemeCat_Desc": "Other Scheme - Index Funds", "TER_Date": "2026-10-20T00:00:00.000Z", "R_TER": "0.6000"},
    ])


def test_ter_percent_string_is_converted_to_decimal():
    assert terf.parse_percent_decimal("0.04", "percent") == pytest.approx(0.0004)
    assert terf.parse_percent_decimal("0.0400", "percent") == pytest.approx(0.0004)
    ok, val, _ = terf.valid_ter("0.04", "percent")
    assert ok and val == pytest.approx(0.0004)


def test_ter_standardizer_on_amfi_rows():
    out = terf.standardize_ter_df(_amfi_ter_rows(), "AMFI_TER_10-2026")
    bees = out[out["Scheme_Name"].eq("Nippon India ETF Nifty 50 BeES")]
    # percent -> decimal, and the latest daily disclosure comes first
    assert bees["TER"].iloc[0] == pytest.approx(0.0005)
    assert (out["TER"] <= rules.ETF_TER_CAP_DECIMAL).all()
    # the category is carried as a category, never as a benchmark
    assert set(out["Scheme_Category"]) >= {"Other Scheme - Index Funds"}
    assert not out["Benchmark_Index"].fillna("").astype(str).str.contains("Scheme -").any()


def test_ter_above_sebi_cap_rejected():
    ok, _, _ = terf.valid_ter("1.51", "percent")          # DEFENCE got an active fund's 1.51%
    assert not ok
    ok, _, _ = enr.valid_ter(0.0151)
    assert not ok
    ok, _, _ = enr.valid_ter(0.04)                          # 4.00% for a Sensex ETF
    assert not ok
    assert enr.valid_ter(0.0016)[0]                         # ITBEES 0.16%


def test_ter_parser_version_forces_refresh(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    status.write_text('{"last_success_run_date": "2026-06-19", "last_attempt_status": "no_source_rows"}')
    monkeypatch.setattr(terf, "STATUS_OUT", status, raising=False)

    class Day5(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 11, 5)
    monkeypatch.setattr(terf, "date", Day5)
    assert terf.should_attempt_refresh([]) is True


# ---------- AUM fetcher -----------------------------------------------------

def test_aaum_lakh_to_crore():
    assert aumf.parse_aum_cr(5705866.48, "lakh") == pytest.approx(57058.66)      # NIFTYBEES
    assert aumf.parse_aum_cr(21078307.49, "lakh") == pytest.approx(210783.07)    # SBI Nifty 50 ETF
    ok, _, val = aumf.validate_etf_aum_cr(21078307.49, "135106", unit="lakh")
    assert ok and val == pytest.approx(210783.07)
    ok, val, _ = enr.valid_etf_aum(210783.07, "135106")
    assert ok and val == pytest.approx(210783.07)


RAW_CACHE_ROWS = [
    ["0", "Nippon India Mutual Fund", "Other Scheme - Other  ETFs", "Nippon India ETF Nifty 50 BeES", "140084",
     "{'ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas': 5705866.48, 'FundOfFundsDomestic': 0}"],
    ["0", "SBI Mutual Fund", "Other Scheme - Other  ETFs", "SBI Nifty 50 ETF", "135106",
     "{'ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas': 21078307.49, 'FundOfFundsDomestic': 0}"],
    ["0", "Aditya Birla Sun Life Mutual Fund", "Other Scheme - Other  ETFs", "Aditya Birla Sun Life Nifty Bank ETF", "147735",
     "{'ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas': 278454.2, 'FundOfFundsDomestic': 0}"],
    ["0", "UTI Mutual Fund", "Other Scheme - Index Funds", "UTI Nifty 50 Index Fund - Growth Option- Direct", "120716",
     "{'ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas': 2078046.5, 'FundOfFundsDomestic': 0}"],
    ["0", "Aditya Birla Sun Life Mutual Fund", "Other Scheme - FoF Domestic", "ABSL Aggressive Hybrid Omni FOF- Regular - IDCW", "132175",
     "{'ExcludingFundOfFundsDomesticButIncludingFundOfFundsOverseas': 0, 'FundOfFundsDomestic': 446.25}"],
]


def _write_cache(path):
    pd.DataFrame(RAW_CACHE_ROWS, columns=["strMFId", "Mfname", "SchemeCat_Desc", "SchemeNAVName",
                                          "AMFI_Code", "AverageAumForTheMonth"]).assign(
        __source_name="AMFI_AUM_Schemewise_fy1_period1_January - March 2026").to_csv(path, index=False)


def test_cached_schemewise_raw_is_read_in_lakh(tmp_path):
    p = tmp_path / "amfi_aum_schemewise_latest_raw.csv"
    _write_cache(p)
    out = aumf.load_cached_schemewise_raw([], path=p).set_index("Scheme_Code")
    assert out.loc["140084", "AUM_Cr"] == pytest.approx(57058.66)
    assert out.loc["135106", "AUM_Cr"] == pytest.approx(210783.07)
    assert out.loc["147735", "AUM_Cr"] == pytest.approx(2784.54)
    assert out.loc["132175", "AUM_Cr"] == pytest.approx(4.46)          # FoF-domestic leg summed
    assert out.loc["120716", "Scheme_Category"] == "Other Scheme - Index Funds"


def _etfs():
    return pd.DataFrame([
        {"Symbol": "NIFTYBEES.NS", "Raw_Symbol": "NIFTYBEES", "Name": "NAM-INDIA - NIFTYBEES", "ISIN": "INF204KB14I2"},
        {"Symbol": "BANKBETA.NS", "Raw_Symbol": "BANKBETA", "Name": "UTIAMC-BANKBETA", "ISIN": "INF789F1AUV1"},
        {"Symbol": "NEWETF.NS", "Raw_Symbol": "NEWETF", "Name": "Nifty 50 Index", "ISIN": ""},
    ])


def _patch_merge_inputs(monkeypatch, tmp_path, enriched):
    monkeypatch.setattr(aumf, "fetch_navall", lambda log_rows: pd.DataFrame())
    monkeypatch.setattr(aumf, "build_nav_mappings", lambda nav: ({}, {}, pd.DataFrame()))
    monkeypatch.setattr(aumf, "load_nav_enriched_symbol_map", lambda log_rows: enriched)
    monkeypatch.setattr(aumf, "MATCH_DIAG_OUT", tmp_path / "diag.csv")


def test_merge_joins_by_code_and_never_borrows(tmp_path, monkeypatch):
    p = tmp_path / "raw.csv"
    _write_cache(p)
    src = aumf.load_cached_schemewise_raw([], path=p)
    enriched = pd.DataFrame([
        {"Symbol": "NIFTYBEES.NS", "AMFI_Scheme_Code": "140084", "AMFI_Scheme_Name": "Nippon India ETF Nifty 50 BeES"},
        # BANKBETA's own code is not in this quarter's file: it must stay blank,
        # not borrow "Aditya Birla Sun Life Nifty Bank ETF" (June: Axis 40,055.67 cr).
        {"Symbol": "BANKBETA.NS", "AMFI_Scheme_Code": "148173", "AMFI_Scheme_Name": "UTI Nifty Bank ETF"},
    ])
    _patch_merge_inputs(monkeypatch, tmp_path, enriched)
    out = aumf.merge_aum_to_etfs(_etfs(), src, []).set_index("Symbol")
    assert out.loc["NIFTYBEES.NS", "AUM Cr"] == pytest.approx(57058.66)
    assert out.loc["NIFTYBEES.NS", "Parser_Version"] == rules.AMFI_PARSER_VERSION
    assert out.loc["BANKBETA.NS", "AUM Cr"] in ("", None) or pd.isna(out.loc["BANKBETA.NS", "AUM Cr"])
    # NEWETF has no code; the closest names are an index fund (skipped) and two
    # Nifty 50 ETFs that tie -> ambiguous, so no AUM.
    assert out.loc["NEWETF.NS", "AUM Cr"] in ("", None) or pd.isna(out.loc["NEWETF.NS", "AUM Cr"])


def test_aum_parser_version_forces_refresh(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    status.write_text('{"last_success_run_date": "2026-11-12"}')
    monkeypatch.setattr(aumf, "STATUS_OUT", status)

    class Day5(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 11, 5)
    monkeypatch.setattr(aumf, "date", Day5)
    assert aumf.should_attempt_refresh([]) is True
    status.write_text('{"last_success_run_date": "2026-11-12", "parser_version": %d}' % rules.AMFI_PARSER_VERSION)
    assert aumf.should_attempt_refresh([]) is False          # day 5 < 10, already v2
