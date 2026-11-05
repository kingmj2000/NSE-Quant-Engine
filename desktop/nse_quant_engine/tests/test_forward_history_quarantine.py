"""Forward returns computed from a quarantined price bar leave the record.

merge_forward_history keeps every row this run cannot recompute, so evidence
for symbols that left the universe survives (no survivorship bias). The same
rule kept bad-bar rows alive: once the price guard drops NV20.NS's zero-volume
12,045.90 bar of 2026-11-05, the three rows computed from it (21D from 10-06,
10D from 10-22, 5D from 10-29, each +901x to +909x) can no longer be recomputed,
and were retained. Measured on a copy of the 2026-11-05 run: All_Avg for those
dates stayed at +189%, Avg_Top10MinusAll at -3.5% / -3.9% / -5.3% (5/10/21D).
With the quarantine log applied: +0.16% / +0.10% / -0.28%, verdict unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import validation_builder as vb  # noqa: E402

LOG_COLUMNS = ["Date", "Symbol", "Close", "Price", "Volume",
               "Last_Traded_Price", "Move_vs_Last_Traded", "Reason"]


def _log(path: Path, rows) -> Path:
    pd.DataFrame(
        [{"Date": d, "Symbol": s, "Close": p, "Price": p, "Volume": 0.0,
          "Last_Traded_Price": ref, "Move_vs_Last_Traded": p / ref - 1,
          "Reason": "zero-volume bar >20% from last traded price"} for d, s, p, ref in rows],
        columns=LOG_COLUMNS,
    ).to_csv(path, index=False)
    return path


def test_quarantine_log_is_read_as_symbol_day_pairs(tmp_path):
    p = _log(tmp_path / "q.csv", [("2026-11-05", "NV20.NS", 12045.90, 13.82)])
    assert vb.load_quarantined_bars(p) == {("NV20.NS", "2026-11-05")}


def test_missing_or_header_only_log_means_nothing_quarantined(tmp_path):
    assert vb.load_quarantined_bars(tmp_path / "absent.csv") == set()
    assert vb.load_quarantined_bars(_log(tmp_path / "empty.csv", [])) == set()


def _fwd(rows):
    """rows: (signal_date, actual_signal_date, symbol, horizon, forward_date, net)."""
    return pd.DataFrame([
        {"Signal_Date": sd, "Signal_Date_Actual_In_Price_File": ad, "Symbol": s,
         "Horizon_Days": h, "Forward_Date": fd, "Net_Forward_Return": r}
        for sd, ad, s, h, fd, r in rows
    ])


def test_rows_on_a_quarantined_bar_are_dropped_and_only_those():
    fwd = _fwd([
        ("2026-10-06", "2026-10-06", "NV20.NS", 21, "2026-11-05", 909.49),
        ("2026-10-22", "2026-10-22", "NV20.NS", 10, "2026-11-05", 901.30),
        ("2026-10-29", "2026-10-29", "NV20.NS", 5, "2026-11-05", 902.66),
        ("2026-10-22", "2026-10-22", "NV20.NS", 5, "2026-10-29", 0.0150),   # clean bar
        ("2026-10-22", "2026-10-22", "NIFTYBEES.NS", 10, "2026-11-05", 0.0310),  # other symbol
        ("2026-11-05", "2026-11-05", "NV20.NS", 5, "2026-11-12", 0.0020),   # signal ON the bad bar
    ])
    out, n = vb.drop_rows_on_quarantined_bars(fwd, {("NV20.NS", "2026-11-05")})
    assert n == 4
    assert sorted(map(tuple, out[["Symbol", "Forward_Date"]].values.tolist())) == [
        ("NIFTYBEES.NS", "2026-11-05"), ("NV20.NS", "2026-10-29")]


def test_no_log_entries_is_a_no_op():
    fwd = _fwd([("2026-10-22", "2026-10-22", "NV20.NS", 10, "2026-11-05", 901.30)])
    out, n = vb.drop_rows_on_quarantined_bars(fwd, set())
    assert n == 0 and out.equals(fwd)


# ------------------------------------------------------------ end to end ----

def _bdays(start, n):
    return list(pd.bdate_range(start, periods=n))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "output"
    data.mkdir()
    out.mkdir()
    for name, path in {
        "SIGNAL_HISTORY": out / "signal_history.csv",
        "RAW_PRICES": data / "raw_prices_latest.csv",
        "FORWARD_OUT": out / "forward_return_history.csv",
        "PER_SYMBOL_SUMMARY_OUT": out / "per_symbol_forward_summary.csv",
        "MISSING_OUT": out / "forward_return_missing_signals.csv",
        "PRICE_QUARANTINE_LOG": data / "price_quarantine_log.csv",
        "SCORING_RULES_CSV": tmp_path / "scoring_rules.csv",   # absent -> defaults
    }.items():
        monkeypatch.setattr(vb, name, path)
    return tmp_path


def _seed_run(base: Path):
    """NV20 history up to 04-Nov (the 05-Nov bar was quarantined), a prior
    forward history holding the +901x row, and a delisted symbol's row."""
    days = _bdays("2026-10-20", 12)            # 10-20 .. 11-04
    px = pd.DataFrame({"Date": [d.date() for d in days], "Symbol": "NV20.NS",
                       "Price": [13.6 + 0.02 * i for i in range(len(days))]})
    px.to_csv(base / "data" / "raw_prices_latest.csv", index=False)
    pd.DataFrame({"Date": ["2026-10-22"], "Symbol": ["NV20.NS"], "Universe": ["ETF"],
                  "Final_Score": [40.0], "Bucket": ["Watch"]}).to_csv(
        base / "output" / "signal_history.csv", index=False)
    prior = pd.DataFrame([
        {"Signal_Date": "2026-10-22", "Signal_Date_Actual_In_Price_File": "2026-10-22",
         "Symbol": "NV20.NS", "Universe": "ETF", "Horizon_Days": 10,
         "Signal_Price_CurrentBasis": 13.35, "Forward_Date": "2026-11-05",
         "Forward_Price_CurrentBasis": 12045.900391, "Gross_Forward_Return": 901.31,
         "Round_Trip_Cost": 0.01, "Net_Forward_Return": 901.30},
        {"Signal_Date": "2026-07-01", "Signal_Date_Actual_In_Price_File": "2026-07-01",
         "Symbol": "BOSCHLTD.NS", "Universe": "Stock", "Horizon_Days": 10,
         "Forward_Date": "2026-07-15", "Net_Forward_Return": 0.021},
    ])
    vb.write_csv_with_headers(prior, base / "output" / "forward_return_history.csv",
                              vb.FORWARD_COLUMNS)


def test_rerun_after_the_guard_removes_the_bad_row_and_keeps_survivors(isolated):
    _seed_run(isolated)
    _log(isolated / "data" / "price_quarantine_log.csv",
         [("2026-11-05", "NV20.NS", 12045.900391, 13.82)])
    vb.main()
    fwd = pd.read_csv(isolated / "output" / "forward_return_history.csv")
    assert fwd["Net_Forward_Return"].abs().max() < 1.0
    assert not ((fwd["Symbol"] == "NV20.NS") & (fwd["Horizon_Days"] == 10)).any()
    assert "BOSCHLTD.NS" in set(fwd["Symbol"])          # survivorship rule intact
    assert ((fwd["Symbol"] == "NV20.NS") & (fwd["Horizon_Days"] == 5)).any()  # clean 5D kept


def test_without_the_log_the_union_would_keep_the_bad_row(isolated):
    """Documents why validation needs the log: the merge alone cannot tell."""
    _seed_run(isolated)
    vb.main()
    fwd = pd.read_csv(isolated / "output" / "forward_return_history.csv")
    assert fwd["Net_Forward_Return"].max() > 900
