"""AMFI TER / tracking import: which number belongs to which ETF (parser v3).

Measured on the 2026-11-05 23:37 run (auto_amfi_ter_tracking_latest.csv, 359 ETFs):

* Matching was by name only, with the fund-house words dropped, and picked one
  "best" row across the TER, tracking-error and tracking-difference streams.
  176 of the 287 TERs came from another AMC's scheme (SBI Nifty Bank ETF took
  Baroda BNP Paribas Nifty Bank ETF's 0.51%); TE landed on 2 ETFs, TD on 30.
* HDFCNIMEG's NAV mapping said "No reliable match", yet its guessed AMFI name
  (Mirae Asset BSE India Defence ETF) was used to match TER.
* SEBI 2026 category names ("Exchange Traded Funds (ETFs) - Equity ETF",
  "Index Funds - Equity Funds") were unknown: 174 stored "benchmarks" were
  category labels and index-fund rows were not recognised as such.
* The TER sheet files a single-plan ETF under the regular OR the direct plan
  and puts 0.00 in the other; only the regular column was read, so 92 of 326
  ETF schemes (every Zerodha, Groww, DSP, Tata, Angel One ETF) had no TER.
* parse_excel_bytes read each sheet twice (7,997 TER rows -> 15,994).
* Only the first non-empty period was used: the November TER sheet had no
  Nippon India / HDFC rows yet and the 31-Oct tracking-error set held SBI's 34
  rows only. The future 30-Nov date was queried six times per run.
* Tracking columns: mfId (3 = ABSL) read as a 0.03 "percent"; since-launch
  figures beat the 1-year ones; a 0 filed for the plan an ETF lacks beat the
  real figure of the other plan.
* 13 ETFs file a total TER above 1.00% (SBI BSE India Defence ETF 7.77% = base
  0.30% + statutory levies 7.20%); 7 of them took another AMC's TER instead
  (SBIDEFETF had Mirae Asset's 0.59%).
"""
from __future__ import annotations

import sys
from datetime import date as real_date
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import etf_aum_auto_fetcher as aumf  # noqa: E402
import etf_ter_tracking_auto_fetcher as terf  # noqa: E402
from core import etf_source_rules as rules  # noqa: E402

ETF_CAT = "Exchange Traded Funds (ETFs) - Equity ETF"
TER_COLS = ["NSDL Scheme Code", "Scheme Name", "Scheme Type", "Scheme Category", "TER Date",
            "Regular Plan - Base Expense Ratio (BER) (%)", "Regular Plan - Total TER (%)",
            "Direct Plan - Base Expense Ratio (BER) (%)", "Direct Plan - Total TER (%)"]


class Nov6(real_date):
    @classmethod
    def today(cls):
        return real_date(2026, 11, 6)


def _ter_sheet(rows):
    """Rows shaped like AMFI's TER_Revised sheet: (code, name, category, date, regular, direct)."""
    return pd.DataFrame([[c, n, "Open Ended", cat, pd.Timestamp(d), 0.0, r, 0.0, dr]
                         for c, n, cat, d, r, dr in rows], columns=TER_COLS)


def _xlsx(df):
    buf = BytesIO()
    df.to_excel(buf, index=False, sheet_name="TER_Revised")
    return buf.getvalue()


def _sources(*frames):
    return terf.combine_source_rows([f for f in frames])


def _ter_rows(rows):
    return terf.standardize_ter_df(_ter_sheet(rows), "AMFI_TER_11-2026")


def _te_rows(rows, when="2026-09-30"):
    df = pd.DataFrame(rows, columns=["Scheme_Name", "Benchmark", "RegularPercent", "DirectPercent", "mfId", "date"])
    return terf.standardize_tracking_error_df(df, f"AMFI_Tracking_Error_{when}")


TD_COLS = ["mfId", "Scheme_Name", "Benchmark", "Y1_R", "Y1_D", "Y3_R", "Y3_D", "Y5_R", "Y5_D",
           "Y10_R", "Y10_D", "ReturnLaunch_R", "ReturnLaunch_D", "date"]


def _td_frame(rows):
    return pd.DataFrame(rows, columns=TD_COLS)


def _build(tmp_path, monkeypatch, etfs, ctx, sources):
    ctx_path = tmp_path / "etf_metadata_enriched.csv"
    pd.DataFrame(ctx).to_csv(ctx_path, index=False)
    monkeypatch.setattr(terf, "ENRICHED_CSV", ctx_path)
    monkeypatch.setattr(terf, "MANUAL_QUALITY", tmp_path / "absent_manual.csv")
    out, diag = terf.build_output_import(pd.DataFrame(etfs), sources, [])
    return out.set_index("Symbol"), diag.set_index("Symbol")


def _num(value):
    return pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]


# ---------- categories ------------------------------------------------------

# Labels as they appear in AMFI's 1-5 Nov 2026 TER sheet.
@pytest.mark.parametrize("label,non_etf", [
    ("Exchange Traded Funds (ETFs) - Equity ETF", False),
    ("Exchange Traded Funds (ETFs) - Gold ETF", False),
    ("Exchange Traded Funds (ETFs) - Debt ETF", False),
    ("Exchange Traded Funds (ETFs) - ETFs investing overseas", False),
    ("Index Funds - Equity Funds", True),
    ("Fund of Funds Scheme (Domestic) - Fund of Funds Scheme (Domestic)", True),
    ("Overseas Fund of Funds - Fund of Funds investing overseas", True),
    ("Income/Debt Oriented Schemes - Liquid Fund", True),
    ("Life Cycle Funds - Life Cycle Fund with Maturity of 10 Years", True),
])
def test_sebi_2026_category_labels(label, non_etf):
    assert rules.is_amfi_category_label(label)
    assert rules.is_non_etf_category(label) is non_etf
    assert rules.benchmark_or_blank(label) == ""


def test_ter_sheet_category_is_not_a_benchmark():
    out = _ter_rows([("AXIS/O/O/EET/20/08/0047", "Axis NIFTY Bank ETF", ETF_CAT, "2026-11-04", 0.24, 0.0)])
    assert out["Benchmark_Index"].fillna("").eq("").all()
    assert out["Scheme_Category"].tolist() == [ETF_CAT]


# ---------- TER sheet -------------------------------------------------------

def test_direct_only_etf_gets_its_direct_plan_ter(tmp_path, monkeypatch):
    monkeypatch.setattr(terf, "DEBUG_DIR", tmp_path)
    sheet = _ter_sheet([
        ("ZERO/O/O/EET/24/01/0001", "Zerodha Nifty 50 ETF", ETF_CAT, "2026-11-04", 0.0, 0.06),
        ("SBIM/O/O/EET/15/07/0001", "SBI Nifty 50 ETF", ETF_CAT, "2026-11-04", 0.049, 0.0),
    ])
    df = terf.parse_excel_bytes(_xlsx(sheet), [], "TER_download_excel")
    assert len(df) == 2                                    # read once, not twice
    out = terf.standardize_ter_df(df, "AMFI_TER_11-2026").set_index("Scheme_Name")
    assert out.loc["Zerodha Nifty 50 ETF", "TER"] == pytest.approx(0.0006)
    assert out.loc["SBI Nifty 50 ETF", "TER"] == pytest.approx(0.00049)


def test_ter_above_one_percent_stays_blank_and_says_why(tmp_path, monkeypatch):
    # SBI BSE India Defence ETF, 4 Nov 2026 sheet: total 7.77 = base 0.30 +
    # statutory levies 7.20 (+ 0.27 brokerage/transaction). The old matcher gave
    # SBIDEFETF Mirae Asset BSE India Defence ETF's 0.59%. An index fund above
    # 1% is another scheme type and is not kept.
    cols = ["NSDL Scheme Code", "Scheme Name", "Scheme Category", "TER Date",
            "Regular Plan - Base Expense Ratio (BER) (%)", "Regular Plan - Statutory Levies (including GST) (%)",
            "Regular Plan - Total TER (%)", "Direct Plan - Base Expense Ratio (BER) (%)",
            "Direct Plan - Statutory Levies (including GST) (%)", "Direct Plan - Total TER (%)"]
    sheet = pd.DataFrame([
        ["SBIM/O/O/EET/26/07/0209", "SBI BSE India Defence ETF", ETF_CAT, pd.Timestamp("2026-11-04"),
         0.30, 7.20, 7.77, 0, 0, 0],
        ["MIRA/O/O/EET/24/09/0070", "Mirae Asset BSE India Defence ETF", ETF_CAT, pd.Timestamp("2026-11-04"),
         0.28, 0.29, 0.59, 0, 0, 0],
        ["UTIM/O/O/IXF/24/01/0001", "UTI BSE India Sector Leaders Index Fund", "Index Funds - Equity Funds",
         pd.Timestamp("2026-11-04"), 1.00, 0.20, 2.22, 0.80, 0.10, 1.40],
    ], columns=cols)
    rows = terf.standardize_ter_df(sheet, "AMFI_TER_11-2026").set_index("Scheme_Name")
    assert pd.isna(rows.loc["SBI BSE India Defence ETF", "TER"])
    assert rows.loc["SBI BSE India Defence ETF", "TER_Rejected"] == pytest.approx(0.0777)
    assert "UTI BSE India Sector Leaders Index Fund" not in rows.index
    etf = {"Symbol": "SBIDEFETF.NS", "Raw_Symbol": "SBIDEFETF", "Name": "SBI BSE India Defence ETF", "ISIN": "INF200KA1TST"}
    ctx = {"Symbol": "SBIDEFETF.NS", "ISIN": "INF200KA1TST", "AMFI_Scheme_Code": 999002,
           "AMFI_Scheme_Name": "SBI BSE India Defence ETF - Regular Plan - Growth", "NAV_Match_Method": "ISIN"}
    out, diag = _build(tmp_path, monkeypatch, [etf], [ctx], _sources(rows.reset_index()))
    assert pd.isna(_num(out.loc["SBIDEFETF.NS", "TER"]))
    assert diag.loc["SBIDEFETF.NS", "TER_Method"] == "Rejected (TER above limit)"
    assert diag.loc["SBIDEFETF.NS", "TER_Scheme"] == "SBI BSE India Defence ETF"
    assert "7.77%" in diag.loc["SBIDEFETF.NS", "Note"]
    assert "base 0.30% + statutory levies 7.20%" in diag.loc["SBIDEFETF.NS", "Note"]


# ---------- matching --------------------------------------------------------

SBIBANKNIF = {"Symbol": "SBIBANKNIF.NS", "Raw_Symbol": "SBIBANKNIF", "Name": "SBI Nifty Bank ETF", "ISIN": "INF200KA1580"}
SBIBANKNIF_CTX = {"Symbol": "SBIBANKNIF.NS", "ISIN": "INF200KA1580", "AMFI_Scheme_Code": 134008,
                  "AMFI_Scheme_Name": "SBI Nifty Bank ETF - Regular Plan - IDCW", "NAV_Match_Method": "ISIN"}


def test_other_amc_scheme_never_supplies_the_number(tmp_path, monkeypatch):
    src = _sources(_ter_rows([
        ("BNPP/O/O/EET/20/06/0001", "Baroda BNP Paribas NIFTY BANK ETF", ETF_CAT, "2026-11-04", 0.51, 0.0),
    ]))
    out, diag = _build(tmp_path, monkeypatch, [SBIBANKNIF], [SBIBANKNIF_CTX], src)
    assert pd.isna(_num(out.loc["SBIBANKNIF.NS", "TER"]))
    assert diag.loc["SBIBANKNIF.NS", "ETF_AMC"] == "sbi"


def test_each_stream_matches_on_its_own(tmp_path, monkeypatch):
    etf = {"Symbol": "BNKETFAXIS.NS", "Raw_Symbol": "BNKETFAXIS", "Name": "AXISAMC-BNKETFAXIS", "ISIN": "INF846K01X63"}
    ctx = {"Symbol": "BNKETFAXIS.NS", "ISIN": "INF846K01X63", "AMFI_Scheme_Code": 148559,
           "AMFI_Scheme_Name": "Axis NIFTY Bank ETF", "NAV_Match_Method": "ISIN"}
    ter = _ter_rows([("AXIS/O/O/EET/20/08/0047", "Axis NIFTY Bank ETF", ETF_CAT, "2026-11-04", 0.24, 0.0)])
    te = _te_rows([["Axis NIFTY Bank ETF", "Nifty Bank TRI", 0.04, None, "53", "30-sep-2026"]])
    td = terf.standardize_tracking_difference_df(_td_frame([
        [53, "Axis NIFTY Bank ETF", "Nifty Bank TRI", -0.28, None, -0.31, None, None, None, None, None, -0.35, None, "01-Oct-2026"],
    ]), "AMFI_Tracking_Difference_01-Oct-2026")
    out, _ = _build(tmp_path, monkeypatch, [etf], [ctx], _sources(ter, te, td))
    row = out.loc["BNKETFAXIS.NS"]
    assert _num(row["TER"]) == pytest.approx(0.0024)
    assert _num(row["Tracking Error"]) == pytest.approx(0.0004)
    assert _num(row["Tracking Difference"]) == pytest.approx(-0.0028)
    assert row["Benchmark Index"] == "Nifty Bank TRI"


def test_unreliable_nav_name_is_not_used_for_matching(tmp_path, monkeypatch):
    etf = {"Symbol": "HDFCNIMEG.NS", "Raw_Symbol": "HDFCNIMEG", "Name": "HDFCAMC - HDFCNIMEG", "ISIN": "INF179KC1KT4"}
    ctx = {"Symbol": "HDFCNIMEG.NS", "ISIN": "INF179KC1KT4", "AMFI_Scheme_Code": np.nan,
           "AMFI_Scheme_Name": "Mirae Asset BSE India Defence ETF", "NAV_Match_Method": "No reliable match"}
    src = _sources(_ter_rows([
        ("MIRA/O/O/EET/24/09/0070", "Mirae Asset BSE India Defence ETF", ETF_CAT, "2026-11-04", 0.44, 0.0),
    ]))
    out, _ = _build(tmp_path, monkeypatch, [etf], [ctx], src)
    assert pd.isna(_num(out.loc["HDFCNIMEG.NS", "TER"]))


def test_index_fund_under_new_label_is_not_an_etf_match(tmp_path, monkeypatch):
    etf = {"Symbol": "ALPHAETF.NS", "Raw_Symbol": "ALPHAETF", "Name": "KOTAKMAMC - ALPHAETF", "ISIN": "INF174KA1TEST"}
    ctx = {"Symbol": "ALPHAETF.NS", "ISIN": "INF174KA1TEST", "AMFI_Scheme_Code": 999001,
           "AMFI_Scheme_Name": "Kotak Nifty Alpha 50 ETF", "NAV_Match_Method": "ISIN"}
    src = _sources(_ter_rows([
        ("KOTM/O/O/IXF/24/01/0001", "Kotak Nifty Alpha 50 Index Fund", "Index Funds - Equity Funds", "2026-11-03", 0.80, 0.0),
    ]))
    out, _ = _build(tmp_path, monkeypatch, [etf], [ctx], src)
    assert pd.isna(_num(out.loc["ALPHAETF.NS", "TER"]))


def test_names_that_differ_in_a_number_or_word_are_different_schemes(tmp_path, monkeypatch):
    etfs = [
        {"Symbol": "EBBETF0431.NS", "Raw_Symbol": "EBBETF0431", "Name": "EDELAMC-EBBETF0431", "ISIN": "INF754K01LE1"},
        {"Symbol": "SBIVALETF.NS", "Raw_Symbol": "SBIVALETF", "Name": "SBIAMC - SBIVALETF", "ISIN": "INF200KB1AX1"},
        SBIBANKNIF,
    ]
    ctx = [
        {"Symbol": "EBBETF0431.NS", "ISIN": "INF754K01LE1", "AMFI_Scheme_Code": 148445,
         "AMFI_Scheme_Name": "BHARAT Bond ETF- April 2031", "NAV_Match_Method": "ISIN"},
        {"Symbol": "SBIVALETF.NS", "ISIN": "INF200KB1AX1", "AMFI_Scheme_Code": 999002,
         "AMFI_Scheme_Name": "SBI Nifty200 Value 30 ETF - Regular Plan - IDCW", "NAV_Match_Method": "ISIN"},
        SBIBANKNIF_CTX,
    ]
    ter = _ter_rows([
        ("EDEL/O/O/DET/19/12/0030", "Bharat Bond ETF - April 2030", "Exchange Traded Funds (ETFs) - Debt ETF", "2026-11-04", 0.0005 * 100, 0.0),
        ("SBIM/O/O/EET/23/09/0175", "SBI Nifty 200 Quality 30 ETF", ETF_CAT, "2026-11-04", 0.33, 0.0),
        ("SBIM/O/O/EET/21/07/0150", "SBI Nifty Private Bank ETF", ETF_CAT, "2026-11-04", 0.18, 0.0),
    ])
    out, _ = _build(tmp_path, monkeypatch, etfs, ctx, _sources(ter))
    for sym in ("EBBETF0431.NS", "SBIVALETF.NS", "SBIBANKNIF.NS"):
        assert pd.isna(_num(out.loc[sym, "TER"])), sym


def test_exact_names_plan_and_option_words(tmp_path, monkeypatch):
    etfs = [
        {"Symbol": "CPSEETF.NS", "Raw_Symbol": "CPSEETF", "Name": "CPSEETF", "ISIN": "INF457M01133"},
        {"Symbol": "CASHIETF.NS", "Raw_Symbol": "CASHIETF", "Name": "ICICIPRAMC - CASHIETF", "ISIN": "INF109KC1TEST"},
        {"Symbol": "LIQUIDIETF.NS", "Raw_Symbol": "LIQUIDIETF", "Name": "ICICIPRAMC-ICICILIQ", "ISIN": "INF109KC2TEST"},
        {"Symbol": "GOLDCASE.NS", "Raw_Symbol": "GOLDCASE", "Name": "ZERODHAAMC-GOLDCASE", "ISIN": "INF0R8F01TEST"},
    ]
    ctx = [
        {"Symbol": "CPSEETF.NS", "ISIN": "INF457M01133", "AMFI_Scheme_Code": 140107,
         "AMFI_Scheme_Name": "CPSE ETF - Direct Plan", "NAV_Match_Method": "ISIN"},
        {"Symbol": "CASHIETF.NS", "ISIN": "INF109KC1TEST", "AMFI_Scheme_Code": 999003,
         "AMFI_Scheme_Name": "ICICI Prudential BSE Liquid Rate ETF - Growth - Regular Plan - Growth", "NAV_Match_Method": "ISIN"},
        {"Symbol": "LIQUIDIETF.NS", "ISIN": "INF109KC2TEST", "AMFI_Scheme_Code": 999004,
         "AMFI_Scheme_Name": "ICICI Prudential BSE Liquid Rate ETF - IDCW - Regular Plan - IDCW", "NAV_Match_Method": "ISIN"},
        {"Symbol": "GOLDCASE.NS", "ISIN": "INF0R8F01TEST", "AMFI_Scheme_Code": 999005,
         "AMFI_Scheme_Name": "Zerodha CASE Gold ETF - Direct Plan", "NAV_Match_Method": "ISIN"},
    ]
    ter = _ter_rows([
        ("NIPP/O/O/EET/14/03/0001", "CPSE ETF", ETF_CAT, "2026-10-30", 0.07, 0.0),
        ("ICIC/O/O/DET/24/12/0190", "ICICI Prudential BSE Liquid Rate ETF - Growth", "Other Scheme - Other  ETFs", "2026-11-03", 0.26, 0.0),
        ("ICIC/O/O/DET/18/03/0059", "ICICI Prudential BSE Liquid Rate ETF - IDCW", "Other Scheme - Other  ETFs", "2026-11-03", 0.36, 0.0),
        ("ZERO/O/O/GET/25/01/0010", "Zerodha Gold ETF", "Exchange Traded Funds (ETFs) - Gold ETF", "2026-11-04", 0.0, 0.35),
    ])
    out, diag = _build(tmp_path, monkeypatch, etfs, ctx, _sources(ter))
    assert _num(out.loc["CPSEETF.NS", "TER"]) == pytest.approx(0.0007)
    assert _num(out.loc["CASHIETF.NS", "TER"]) == pytest.approx(0.0026)
    assert _num(out.loc["LIQUIDIETF.NS", "TER"]) == pytest.approx(0.0036)
    assert _num(out.loc["GOLDCASE.NS", "TER"]) == pytest.approx(0.0035)
    assert diag.loc["GOLDCASE.NS", "TER_Method"] == "Name_Tokens_Same_AMC"


def test_newest_disclosure_of_a_scheme_wins(tmp_path, monkeypatch):
    oct_rows = _ter_rows([("SBIM/O/O/EET/21/07/0140", "SBI Nifty Bank ETF", ETF_CAT, "2026-10-30", 0.25, 0.0)])
    nov_rows = _ter_rows([("SBIM/O/O/EET/21/07/0140", "SBI Nifty Bank ETF", ETF_CAT, "2026-11-04", 0.22, 0.0)])
    out, _ = _build(tmp_path, monkeypatch, [SBIBANKNIF], [SBIBANKNIF_CTX], _sources(oct_rows, nov_rows))
    assert _num(out.loc["SBIBANKNIF.NS", "TER"]) == pytest.approx(0.0022)


# ---------- tracking columns -------------------------------------------------

def test_tracking_error_columns_real_rows():
    # AMFI tracking error, 30 Sep 2026 (real rows): Angel One files the direct
    # plan only; Bajaj Finserv files 0.00 for the regular plan it does not have.
    out = _te_rows([
        ["Bajaj Finserv BSE Top 10 Banks ETF", "BSE Top 10 Banks TRI", 0.00, 0.06, "75", "30-sep-2026"],
        ["ANGEL ONE NIFTY 50 ETF", "NIFTY 50 TRI", None, 0.04, "80", "30-sep-2026"],
        ["Aditya Birla Sun Life BSE Total Market ETF", "BSE Total Market TRI", None, None, "3", "30-sep-2026"],
    ]).set_index("Scheme_Name")
    assert out.loc["Bajaj Finserv BSE Top 10 Banks ETF", "Tracking_Error"] == pytest.approx(0.0006)
    assert out.loc["ANGEL ONE NIFTY 50 ETF", "Tracking_Error"] == pytest.approx(0.0004)
    # No figure filed: mfId 3 is an AMC id, not a 0.03% tracking error.
    assert "Aditya Birla Sun Life BSE Total Market ETF" not in out.index


def test_tracking_difference_prefers_one_year():
    # AMFI tracking difference, 01-Oct-2026 (real row): 1Y -0.04, since launch +0.01.
    out = terf.standardize_tracking_difference_df(_td_frame([
        [53, "Axis NIFTY 50 ETF", "Nifty 50 TRI", -0.04, None, -0.08, None, -0.06, None, None, None, 0.01, None, "01-Oct-2026"],
        [3, "Aditya Birla Sun Life BSE Total Market ETF", "BSE Total Market TRI", None, None, None, None, None, None, None, None, None, None, "01-Oct-2026"],
    ]), "AMFI_Tracking_Difference_01-Oct-2026").set_index("Scheme_Name")
    assert out.loc["Axis NIFTY 50 ETF", "Tracking_Difference"] == pytest.approx(-0.0004)
    assert "Aditya Birla Sun Life BSE Total Market ETF" not in out.index


# ---------- periods -----------------------------------------------------------

class _TerClient:
    """November sheet without Nippon India (as on 5 Nov 2026); October has it."""

    def __init__(self):
        self.calls = []

    def months(self, year):
        return [{"MonthNumber": "11-2026"}, {"MonthNumber": "10-2026"}, {"MonthNumber": "09-2026"}]

    def download_excel(self, month, year=None, mf_id=None):
        self.calls.append(month)
        if month == "11-2026":
            return _xlsx(_ter_sheet([("SBIM/O/O/EET/15/07/0001", "SBI Nifty 50 ETF", ETF_CAT, "2026-11-04", 0.049, 0.0)]))
        if month == "10-2026":
            return _xlsx(_ter_sheet([
                ("SBIM/O/O/EET/15/07/0001", "SBI Nifty 50 ETF", ETF_CAT, "2026-10-30", 0.07, 0.0),
                ("NIPP/O/O/EET/01/12/0001", "Nippon India ETF Nifty 50 BeES", ETF_CAT, "2026-10-30", 0.04, 0.0),
            ]))
        return b""

    def fetch(self, month, year=None, mf_id=None, as_df=False):
        return []


def test_ter_combines_the_newest_months(tmp_path, monkeypatch):
    monkeypatch.setattr(terf, "DEBUG_DIR", tmp_path)
    monkeypatch.setattr(terf, "date", Nov6)
    client = _TerClient()
    out = terf.fetch_ter_sources([], [client])
    assert "Nippon India ETF Nifty 50 BeES" in set(out["Scheme_Name"])
    assert client.calls[:2] == ["11-2026", "10-2026"]
    assert "09-2026" not in client.calls                     # two months are enough


class _TrackingClient:
    def __init__(self):
        self.te_calls = []
        self.td_calls = []

    def error(self, date, mf_id=None, as_df=True):
        self.te_calls.append(date)
        if date == "31-oct-2026":
            return pd.DataFrame([["SBI Nifty 50 ETF", "NIFTY 50", 0.02, 0.02, "22", date]],
                                columns=["Scheme_Name", "Benchmark", "RegularPercent", "DirectPercent", "mfId", "date"])
        if date == "30-sep-2026":
            return pd.DataFrame([["Axis NIFTY 50 ETF", "Nifty 50 TRI", 0.03, None, "53", date]],
                                columns=["Scheme_Name", "Benchmark", "RegularPercent", "DirectPercent", "mfId", "date"])
        return pd.DataFrame()

    def difference(self, month, mf_id=None, as_df=True):
        self.td_calls.append(month)
        if month == "01-Oct-2026":
            return _td_frame([[53, "Axis NIFTY 50 ETF", "Nifty 50 TRI", -0.04, None, None, None, None, None,
                               None, None, 0.01, None, month]])
        return pd.DataFrame()


def test_tracking_combines_periods_and_skips_future_dates(tmp_path, monkeypatch):
    monkeypatch.setattr(terf, "DEBUG_DIR", tmp_path)
    monkeypatch.setattr(terf, "date", Nov6)
    client = _TrackingClient()
    te, td = terf.fetch_tracking_sources([], [client])
    assert set(te["Scheme_Name"]) == {"SBI Nifty 50 ETF", "Axis NIFTY 50 ETF"}
    assert "30-nov-2026" not in client.te_calls               # month end still ahead
    assert client.td_calls.count("01-Nov-2026") <= 1          # one empty answer is enough
    assert td["Scheme_Name"].tolist() == ["Axis NIFTY 50 ETF"]


def test_empty_answer_is_not_asked_again():
    calls = []

    class Client:
        def error(self, **kwargs):
            calls.append(kwargs)
            return pd.DataFrame()

    attempts = [{"date": "31-oct-2026", "mf_id": "all", "as_df": True},
                {"date": "31-oct-2026", "mf_id": "All", "as_df": True},
                {"date": "31-oct-2026", "as_df": True}]
    assert terf.call_method(Client(), "error", attempts, [], "TrackingError").empty
    assert len(calls) == 1


# ---------- schedules -----------------------------------------------------------

def test_ter_refreshes_once_inside_the_window(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    latest = tmp_path / "auto_amfi_ter_tracking_latest.csv"
    latest.write_text("Symbol\n", encoding="utf-8")
    monkeypatch.setattr(terf, "STATUS_OUT", status)
    monkeypatch.setattr(terf, "AUTO_IMPORT_LATEST", latest)
    monkeypatch.setattr(terf, "FORCE_REFRESH", False)

    class Nov12(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 11, 12)
    monkeypatch.setattr(terf, "date", Nov12)
    # The parser-bump run of 6 Nov saw only the AMCs that had filed by then.
    status.write_text('{"last_success_run_date": "2026-11-06", "parser_version": %d}' % rules.AMFI_PARSER_VERSION)
    assert terf.should_attempt_refresh([]) is True
    status.write_text('{"last_success_run_date": "2026-11-12", "parser_version": %d}' % rules.AMFI_PARSER_VERSION)
    assert terf.should_attempt_refresh([]) is False


def test_aum_cache_fallback_retries_live(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    monkeypatch.setattr(aumf, "STATUS_OUT", status)
    monkeypatch.setattr(aumf, "FORCE_REFRESH", False)

    class Nov5(real_date):
        @classmethod
        def today(cls):
            return real_date(2026, 11, 5)
    monkeypatch.setattr(aumf, "date", Nov5)
    # 2026-11-05 23:29: amfipy import failed, the cached Jan-Mar quarter was used.
    status.write_text('{"last_attempt_status": "success_from_cache", "last_success_run_date": "2026-06-17", '
                      '"parser_version": %d}' % rules.AMFI_PARSER_VERSION)
    assert aumf.should_attempt_refresh([]) is True
    status.write_text('{"last_attempt_status": "success_from_cache", "last_success_run_date": "2026-06-17", '
                      '"last_live_attempt_date": "2026-11-05", "parser_version": %d}' % rules.AMFI_PARSER_VERSION)
    assert aumf.should_attempt_refresh([]) is False          # tried live today already
