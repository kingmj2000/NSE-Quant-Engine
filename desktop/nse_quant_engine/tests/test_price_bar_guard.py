"""Zero-volume price bars that jump are quarantined before anything scores them.

2026-11-05: Yahoo served NV20.NS at 12,045.90 with volume 0, against 13.82 (volume
104,993) the day before, i.e. roughly the index level. Nothing checked it:
NV20 got a +90,950% 21-day return, Opportunity_Score 100 and an entry zone of
12,045, and validation_builder turned it into +901x forward returns that pulled
the all-symbol averages to +189% for 2026-10-22 (10D) and 2026-10-29 (5D).

On NSE a bar with no trades cannot move the price (the last close is carried
forward), so the guard drops only zero-volume bars that move >20% from the last
TRADED price. Traded bars are never touched, whatever their move.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nse_quant_engine as eng  # noqa: E402


def _bars(symbol, rows):
    """rows: (date, price, volume)."""
    return pd.DataFrame({
        "Date": [pd.Timestamp(d).date() for d, _, _ in rows],
        "Open": [p for _, p, _ in rows], "High": [p for _, p, _ in rows],
        "Low": [p for _, p, _ in rows], "Close": [p for _, p, _ in rows],
        "Adj Close": [p for _, p, _ in rows], "Volume": [v for _, _, v in rows],
        "Symbol": symbol, "Price": [p for _, p, _ in rows],
    })


NV20_REPLICA = [  # the real 2026-11-05 shape
    ("2026-11-02", 13.61, 384564.0),
    ("2026-11-03", 13.77, 99283.0),
    ("2026-11-04", 13.82, 104993.0),
    ("2026-11-05", 12045.900391, 0.0),
]


def test_zero_volume_index_level_bar_is_quarantined():
    clean, q = eng.quarantine_untraded_price_jumps(_bars("NV20.NS", NV20_REPLICA))
    assert list(pd.to_datetime(clean["Date"]).dt.strftime("%Y-%m-%d")) == [
        "2026-11-02", "2026-11-03", "2026-11-04"]
    assert len(q) == 1
    row = q.iloc[0]
    assert row["Symbol"] == "NV20.NS" and str(row["Date"]) == "2026-11-05"
    assert row["Last_Traded_Price"] == 13.82
    assert row["Move_vs_Last_Traded"] > 800


def test_traded_bars_are_never_dropped_even_on_huge_moves():
    # An unadjusted 1:10 split and a real crash both trade: they are market data.
    rows = [("2026-02-25", 152.0, 1000.0), ("2026-02-26", 151.7, 900.0),
            ("2026-02-27", 15.17, 273591.0), ("2026-03-02", 15.2, 5000.0)]
    clean, q = eng.quarantine_untraded_price_jumps(_bars("SPLIT.NS", rows))
    assert len(clean) == 4 and q.empty


def test_quiet_zero_volume_days_are_kept():
    # Illiquid ETF: no trades, close carried forward (or a small drift).
    rows = [("2026-10-01", 100.0, 50.0), ("2026-10-02", 100.0, 0.0),
            ("2026-10-05", 101.5, 0.0), ("2026-10-06", 102.0, 10.0)]
    clean, q = eng.quarantine_untraded_price_jumps(_bars("ILLIQ.NS", rows))
    assert len(clean) == 4 and q.empty


def test_reference_is_last_traded_bar_not_last_bar():
    # Two bad zero-volume bars in a row: the second must not be judged against
    # the first. The next traded bar is kept.
    rows = [("2026-10-01", 50.0, 10.0), ("2026-10-02", 80.0, 0.0),
            ("2026-10-05", 81.0, 0.0), ("2026-10-06", 50.5, 20.0)]
    clean, q = eng.quarantine_untraded_price_jumps(_bars("TWO.NS", rows))
    assert sorted(q["Date"].astype(str)) == ["2026-10-02", "2026-10-05"]
    assert list(clean["Price"]) == [50.0, 50.5]


def test_no_traded_reference_means_no_judgement():
    # A listing placeholder series that has never traded has no reference price.
    rows = [("2026-07-20", 10.0, 0.0), ("2026-07-21", 27.9, 0.0), ("2026-07-23", 27.3, 75735.0)]
    clean, q = eng.quarantine_untraded_price_jumps(_bars("NEW.NS", rows))
    assert len(clean) == 3 and q.empty


def test_index_and_benchmark_symbols_are_exempt():
    # Yahoo reports no volume for indices.
    rows = [("2026-11-04", 23960.6, 100.0), ("2026-11-05", 32000.0, 0.0)]
    frame = pd.concat([_bars("^NSEI", rows), _bars("BENCH", rows)], ignore_index=True)
    clean, q = eng.quarantine_untraded_price_jumps(frame, skip_symbols={"BENCH"})
    assert len(clean) == 4 and q.empty


def test_other_symbols_and_row_order_survive():
    frame = pd.concat([_bars("NV20.NS", NV20_REPLICA),
                       _bars("NIFTYBEES.NS", [("2026-11-04", 272.81, 5e6), ("2026-11-05", 279.36, 6e6)])],
                      ignore_index=True)
    clean, q = eng.quarantine_untraded_price_jumps(frame)
    assert len(q) == 1
    assert len(clean) == len(frame) - 1
    assert list(clean.index) == sorted(clean.index)          # original order kept
    assert set(clean[clean.Symbol == "NIFTYBEES.NS"]["Price"]) == {272.81, 279.36}


def test_download_path_applies_the_guard_and_logs(tmp_path, monkeypatch):
    """download_price_data is what raw_prices_latest.csv, the cache and scoring use."""
    frames = [_bars("NV20.NS", NV20_REPLICA),
              _bars("^NSEI", [("2026-11-04", 23960.6, 0.0), ("2026-11-05", 24536.6, 0.0)])]

    def _fake_batch(symbols, period):
        diags = [{"Symbol": s, "Download_Status": "Success"} for s in symbols]
        return [f for f in frames if f["Symbol"].iloc[0] in symbols], diags, []

    monkeypatch.setattr(eng, "fetch_batch_symbols", _fake_batch)
    monkeypatch.setattr(eng, "DOWNLOAD_DIAGNOSTICS_CSV", tmp_path / "diag.csv")
    monkeypatch.setattr(eng, "PRICE_QUARANTINE_CSV", tmp_path / "price_quarantine_log.csv")

    prices, bench, failed = eng.download_price_data(["NV20.NS"], "^NSEI")
    assert pd.to_datetime(prices["Date"]).max() == pd.Timestamp("2026-11-04")
    assert prices["Price"].max() < 20
    assert len(bench) == 2                                     # index untouched
    log = pd.read_csv(tmp_path / "price_quarantine_log.csv")
    assert list(log["Symbol"]) == ["NV20.NS"]


def test_quarantine_log_is_rewritten_header_only_when_clean(tmp_path, monkeypatch):
    path = tmp_path / "price_quarantine_log.csv"
    path.write_text("stale,content\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(eng, "PRICE_QUARANTINE_CSV", path)
    eng._report_price_quarantine(pd.DataFrame(columns=eng.QUARANTINE_COLUMNS))
    assert path.read_text(encoding="utf-8").strip() == ",".join(eng.QUARANTINE_COLUMNS)


def test_cache_update_drops_archived_bad_bars(tmp_path, monkeypatch):
    cache = tmp_path / "price_history_cache.csv"
    _bars("NV20.NS", NV20_REPLICA).to_csv(cache, index=False)      # bad bar already archived
    monkeypatch.setattr(eng, "PRICE_CACHE_CSV", cache)
    eng.update_price_cache(_bars("NV20.NS", NV20_REPLICA[:3]))      # guarded download
    out = pd.read_csv(cache)
    assert out["Price"].max() < 20 and len(out) == 3


def test_forward_returns_no_longer_see_the_bad_bar():
    """The validation input is clean: no +900x forward return can be formed."""
    clean, _ = eng.quarantine_untraded_price_jumps(_bars("NV20.NS", NV20_REPLICA))
    p = clean.sort_values("Date")["Price"].to_numpy()
    worst = np.max(p[1:] / p[:-1] - 1)
    assert worst < 0.05
