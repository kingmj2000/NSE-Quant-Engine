"""
Hold-Horizon Optimizer (Step 3).

Per candidate, sweep a horizon grid and compute:
  * expected forward return curve (historical median + IQR of realised H-day returns)
  * risk curve (realised H-day vol × √h, and max historical drawdown at horizon)
  * "sharpe-like" ratio = median_return / (downside_vol + eps)

The optimiser picks the horizon that maximises the sharpe-like ratio subject to
a soft risk cap. Pure historical statistics — no forward-looking data. All windows
are realised (backward-only rolling) so there is no lookahead leak.

Called from `trade_plan_builder.py` post trade-plan generation; every failure is
caught by the caller so the base pipeline never regresses.
"""
from __future__ import annotations

from typing import Iterable
import numpy as np
import pandas as pd

DEFAULT_HORIZONS: tuple[int, ...] = (3, 5, 10, 21, 42, 63)
DEFAULT_HIST_DAYS: int = 250
DEFAULT_RISK_CAP_PCT: float = 6.0
_EPS = 1e-6


# ── curves ──────────────────────────────────────────────────────────────────
def _closes(prices_long: pd.DataFrame, symbol: str, hist_days: int) -> pd.Series:
    if prices_long is None or prices_long.empty:
        return pd.Series(dtype=float)
    df = prices_long[prices_long["Symbol"] == symbol].copy()
    if df.empty:
        return pd.Series(dtype=float)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).sort_values("Date")
    c = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if hist_days and len(c) > hist_days + max(DEFAULT_HORIZONS) + 5:
        c = c.tail(hist_days + max(DEFAULT_HORIZONS) + 5)
    return c.reset_index(drop=True)


def _horizon_returns(closes: pd.Series, h: int) -> pd.Series:
    """Realised H-day returns, computed only from history (no shift(-N))."""
    if len(closes) <= h + 1:
        return pd.Series(dtype=float)
    r = closes.pct_change(h).dropna()
    return r


def expected_return_curve(closes: pd.Series, horizons: Iterable[int]) -> pd.DataFrame:
    rows = []
    for h in horizons:
        r = _horizon_returns(closes, h)
        if r.empty:
            rows.append({"H": h, "median_ret": np.nan, "q25": np.nan, "q75": np.nan,
                         "n": 0})
            continue
        rows.append({
            "H": h,
            "median_ret": float(r.median()),
            "q25": float(r.quantile(0.25)),
            "q75": float(r.quantile(0.75)),
            "n": int(len(r)),
        })
    return pd.DataFrame(rows)


def risk_curve(closes: pd.Series, horizons: Iterable[int]) -> pd.DataFrame:
    rows = []
    daily = closes.pct_change().dropna()
    d_vol = float(daily.std(ddof=0)) if len(daily) > 5 else np.nan
    for h in horizons:
        r = _horizon_returns(closes, h)
        if r.empty:
            rows.append({"H": h, "vol_h": np.nan, "downside_vol": np.nan,
                         "max_dd_h": np.nan})
            continue
        neg = r[r < 0]
        rows.append({
            "H": h,
            "vol_h": (d_vol * np.sqrt(h)) if pd.notna(d_vol) else np.nan,
            "downside_vol": float(neg.std(ddof=0)) if len(neg) > 3 else float(r.std(ddof=0)),
            "max_dd_h": float(r.min()),
        })
    return pd.DataFrame(rows)


# ── optimiser ───────────────────────────────────────────────────────────────
def optimal_horizon(prices_long: pd.DataFrame,
                    symbol: str,
                    horizons: Iterable[int] = DEFAULT_HORIZONS,
                    hist_days: int = DEFAULT_HIST_DAYS,
                    risk_cap_pct: float = DEFAULT_RISK_CAP_PCT) -> dict:
    """Return {Symbol, Rec_Horizon_Days, Exp_Ret_%, Downside_Vol_%, Sharpe_like,
              curve} or a NaN record when history is too short."""
    empty = {"Symbol": symbol, "Rec_Horizon_Days": np.nan, "Exp_Ret_%": np.nan,
             "Downside_Vol_%": np.nan, "Sharpe_like": np.nan,
             "Horizons": list(horizons), "Exp_Ret_Curve": []}
    closes = _closes(prices_long, symbol, hist_days)
    if closes.empty or len(closes) < min(horizons) + 5:
        return empty

    exp_df = expected_return_curve(closes, horizons)
    risk_df = risk_curve(closes, horizons)
    if exp_df.empty or risk_df.empty:
        return empty
    curve = exp_df.merge(risk_df, on="H")
    if curve["median_ret"].isna().all():
        return empty

    cap = risk_cap_pct / 100.0
    curve["sharpe_like"] = curve["median_ret"] / (curve["downside_vol"].abs() + _EPS)
    curve["risk_ok"] = curve["downside_vol"].abs() * np.sqrt(curve["H"]) <= cap

    valid = curve[curve["risk_ok"] & curve["median_ret"].notna()].copy()
    if valid.empty:
        # fall back: pick the best sharpe_like even if it breaches cap
        valid = curve[curve["median_ret"].notna()].copy()
    if valid.empty:
        return empty
    row = valid.sort_values("sharpe_like", ascending=False).iloc[0]

    return {
        "Symbol": symbol,
        "Rec_Horizon_Days": int(row["H"]),
        "Exp_Ret_%": float(row["median_ret"] * 100.0),
        "Downside_Vol_%": float(row["downside_vol"] * 100.0),
        "Sharpe_like": float(row["sharpe_like"]),
        "Horizons": [int(h) for h in curve["H"].tolist()],
        "Exp_Ret_Curve": [None if pd.isna(v) else round(float(v) * 100.0, 3)
                          for v in curve["median_ret"].tolist()],
    }


def optimise_batch(prices_long: pd.DataFrame,
                   symbols: Iterable[str],
                   horizons: Iterable[int] = DEFAULT_HORIZONS,
                   hist_days: int = DEFAULT_HIST_DAYS,
                   risk_cap_pct: float = DEFAULT_RISK_CAP_PCT) -> pd.DataFrame:
    recs = [optimal_horizon(prices_long, s, horizons, hist_days, risk_cap_pct)
            for s in symbols]
    return pd.DataFrame(recs)
