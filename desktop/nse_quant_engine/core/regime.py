"""India VIX regime helpers. Scales risk penalties and absolute filters."""
from __future__ import annotations
import numpy as np
import pandas as pd
from . import config as C

LOW_PCT = 30.0
HIGH_PCT = 70.0


def percentile_252d(series: pd.Series) -> float:
    s = pd.to_numeric(series, errors="coerce").dropna().tail(252)
    if len(s) < 20 or pd.isna(s.iloc[-1]):
        return float("nan")
    last = float(s.iloc[-1])
    return float((s < last).mean() * 100.0)


def classify(vix_pct: float) -> str:
    if pd.isna(vix_pct):
        return "UNKNOWN"
    if vix_pct < LOW_PCT:
        return "LOW"
    if vix_pct > HIGH_PCT:
        return "HIGH"
    return "MID"


def scale_for_regime(regime: str) -> dict:
    """Return multipliers to apply to scoring penalties/filters."""
    if regime == "HIGH":
        return {"vol_penalty_mult": 1.30, "min_abs_ret_21d_add": 0.01, "rs_fail_mult": 0.85}
    if regime == "LOW":
        return {"vol_penalty_mult": 0.85, "min_abs_ret_21d_add": -0.005, "rs_fail_mult": 0.95}
    return {"vol_penalty_mult": 1.0, "min_abs_ret_21d_add": 0.0, "rs_fail_mult": C.RS_FAIL_MULT}


def breadth_pct_above_ma(price_history: pd.DataFrame, window: int = 50) -> float:
    """price_history: long form Date,Symbol,Price. Returns % of symbols above their N-day MA."""
    if price_history is None or price_history.empty:
        return float("nan")
    df = price_history.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Price"]).sort_values("Date")
    wide = df.pivot_table(index="Date", columns="Symbol", values="Price", aggfunc="last")
    if wide.shape[0] < window + 1:
        return float("nan")
    ma = wide.rolling(window).mean()
    last_close = wide.iloc[-1]
    last_ma = ma.iloc[-1]
    above = (last_close > last_ma).sum()
    total = last_ma.notna().sum()
    return float(above / total * 100.0) if total else float("nan")
