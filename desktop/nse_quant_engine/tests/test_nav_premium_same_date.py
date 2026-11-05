"""NAV premium/discount compares price and NAV taken on the SAME date.

AMFI strikes NAV at the close of NAV_Date and publishes it late in the evening,
so a run during market hours (or before the evening NAV file) pairs today's
price with yesterday's NAV. Measured on the 2026-11-05 run: every one of 358
NAVs was dated 04-Nov, prices were 05-Nov, Nifty rose 2.40% that day.

- NIFTYBEES: 279.36 / 273.3918 - 1 = +2.18% shown; true (04-Nov close
  272.81 vs 04-Nov NAV) -0.21%.
- 98 of 282 ETFs breached the 2% premium limit; on matching dates, 8 did.

The premium feeds 30% of ETF_Quality_Score, so the cross-date version
penalised exactly the ETFs that had risen most that day.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import nse_quant_engine as eng  # noqa: E402


def _prices(rows):
    """rows: (symbol, date, close)."""
    return pd.DataFrame({
        "Date": [pd.Timestamp(d) for _, d, _ in rows],
        "Symbol": [s for s, _, _ in rows],
        "Close": [c for _, _, c in rows],
        "Price": [c for _, _, c in rows],
    })


def _latest(rows):
    """rows: (symbol, latest date, latest price, NAV, NAV_Date text)."""
    return pd.DataFrame({
        "Symbol": [r[0] for r in rows],
        "Date": [pd.Timestamp(r[1]) for r in rows],
        "Price": [r[2] for r in rows],
        "NAV": [r[3] for r in rows],
        "NAV_Date": [r[4] for r in rows],
    })


# The real 2026-11-05 numbers (raw_prices_latest.csv / etf_quality_latest.csv).
NIFTYBEES_PRICES = [
    ("NIFTYBEES.NS", "2026-11-03", 270.119995),
    ("NIFTYBEES.NS", "2026-11-04", 272.809998),
    ("NIFTYBEES.NS", "2026-11-05", 279.359985),
]
BANKBEES_PRICES = [
    ("BANKBEES.NS", "2026-11-04", 578.210022),
    ("BANKBEES.NS", "2026-11-05", 597.890015),
]


def test_nav_from_yesterday_is_compared_with_yesterdays_close():
    prices = _prices(NIFTYBEES_PRICES + BANKBEES_PRICES)
    latest = _latest([
        ("NIFTYBEES.NS", "2026-11-05", 279.359985, 273.3918, "04-Nov-2026"),
        ("BANKBEES.NS", "2026-11-05", 597.890015, 579.5330, "04-Nov-2026"),
    ])
    prem = eng.nav_premium_same_date(latest, prices)

    # What the old cross-date formula produced: both over the 2% limit.
    old = latest["Price"] / latest["NAV"] - 1
    assert old.round(4).tolist() == [0.0218, 0.0317]

    assert prem.iloc[0] == pytest.approx(272.809998 / 273.3918 - 1)   # -0.21%
    assert prem.iloc[1] == pytest.approx(578.210022 / 579.5330 - 1)   # -0.23%
    assert (prem.abs() < 0.005).all()


def test_same_day_nav_uses_the_latest_price_unchanged():
    # An evening run: NAV and price share the date. Behaviour must not change,
    # even if the history frame disagrees with the latest row.
    prices = _prices([("NIFTYBEES.NS", "2026-11-05", 999.0)])
    latest = _latest([("NIFTYBEES.NS", "2026-11-05", 279.36, 279.10, "05-Nov-2026")])
    prem = eng.nav_premium_same_date(latest, prices)
    assert prem.iloc[0] == pytest.approx(279.36 / 279.10 - 1)


def test_no_bar_on_the_nav_date_means_unknown_not_a_cross_date_guess():
    # NAV dated 03-Nov, but this ETF did not trade (no bar) on 03-Nov.
    prices = _prices([
        ("THINETF.NS", "2026-11-04", 101.0),
        ("THINETF.NS", "2026-11-05", 104.0),
    ])
    latest = _latest([("THINETF.NS", "2026-11-05", 104.0, 100.0, "03-Nov-2026")])
    prem = eng.nav_premium_same_date(latest, prices)
    assert np.isnan(prem.iloc[0])
    # NaN is neutral downstream (score_lower_is_better fills 50) and never
    # trips the "High NAV premium/discount" flag.


def test_missing_or_zero_nav_gives_nan():
    prices = _prices(NIFTYBEES_PRICES)
    latest = _latest([
        ("NIFTYBEES.NS", "2026-11-05", 279.36, np.nan, "04-Nov-2026"),
        ("NIFTYBEES.NS", "2026-11-05", 279.36, 0.0, "04-Nov-2026"),
    ])
    prem = eng.nav_premium_same_date(latest, prices)
    assert prem.isna().all()


def test_missing_nav_date_falls_back_to_latest_price():
    latest = _latest([
        ("NIFTYBEES.NS", "2026-11-05", 279.36, 279.10, np.nan),
        ("BANKBEES.NS", "2026-11-05", 597.89, 590.00, ""),
    ])
    prem = eng.nav_premium_same_date(latest, _prices(NIFTYBEES_PRICES))
    assert prem.iloc[0] == pytest.approx(279.36 / 279.10 - 1)
    assert prem.iloc[1] == pytest.approx(597.89 / 590.00 - 1)
    # No NAV_Date column at all: same fallback.
    no_col = latest.drop(columns=["NAV_Date"])
    assert eng.nav_premium_same_date(no_col, None).iloc[0] == pytest.approx(279.36 / 279.10 - 1)


# ISO must not go through day-first parsing, which reads 2026-11-04 as 11 April.
@pytest.mark.parametrize("text", ["04-Nov-2026", "2026-11-04", "2026-11-04 00:00:00",
                                  "04/11/2026", " 04-Nov-2026 "])
def test_nav_date_formats_resolve_to_the_same_day(text):
    latest = _latest([("NIFTYBEES.NS", "2026-11-05", 279.359985, 273.3918, text)])
    prem = eng.nav_premium_same_date(latest, _prices(NIFTYBEES_PRICES))
    assert prem.iloc[0] == pytest.approx(272.809998 / 273.3918 - 1)


def test_close_column_preferred_and_price_used_when_close_missing():
    prices = _prices(NIFTYBEES_PRICES)
    prices.loc[prices["Date"].eq(pd.Timestamp("2026-11-04")), "Close"] = np.nan
    latest = _latest([("NIFTYBEES.NS", "2026-11-05", 279.359985, 273.3918, "04-Nov-2026")])
    prem = eng.nav_premium_same_date(latest, prices)
    assert prem.iloc[0] == pytest.approx(272.809998 / 273.3918 - 1)


def test_merge_etf_quality_writes_the_same_date_premium():
    latest = _latest([
        ("NIFTYBEES.NS", "2026-11-05", 279.359985, np.nan, ""),
        ("RELIANCE.NS", "2026-11-05", 1500.0, np.nan, ""),
    ]).drop(columns=["NAV", "NAV_Date"])
    quality = pd.DataFrame({
        "Symbol": ["NIFTYBEES.NS"],
        "NAV": [273.3918],
        "NAV_Date": ["04-Nov-2026"],
    })
    out = eng.merge_etf_quality(latest, quality, _prices(NIFTYBEES_PRICES))
    by_sym = out.set_index("Symbol")["NAV_Premium_Discount"]
    assert by_sym["NIFTYBEES.NS"] == pytest.approx(272.809998 / 273.3918 - 1)
    assert np.isnan(by_sym["RELIANCE.NS"])          # stocks have no NAV


def test_flag_count_on_a_rally_day_replica():
    """Ten ETFs, NAVs a day old, market +2.4% overnight: old formula flags all."""
    rows_p, rows_l = [], []
    for i in range(10):
        sym = f"ETF{i}.NS"
        nav = 100.0 + i
        rows_p += [(sym, "2026-11-04", nav * 0.999), (sym, "2026-11-05", nav * 0.999 * 1.024)]
        rows_l.append((sym, "2026-11-05", nav * 0.999 * 1.024, nav, "04-Nov-2026"))
    latest = _latest(rows_l)
    prem = eng.nav_premium_same_date(latest, _prices(rows_p))
    old = latest["Price"] / latest["NAV"] - 1
    limit = eng.DEFAULT_RULES["Abs_NAV_Premium_Max"]   # 0.02
    assert int((old.abs() > limit).sum()) == 10
    assert int((prem.abs() > limit).sum()) == 0
