"""ETF microstructure: traded value, intraday spread proxy, iNAV premium z-score."""
from __future__ import annotations
import numpy as np
import pandas as pd

LIQUIDITY_MIN_INR = 2_00_00_000  # 2 crore/day default floor


def traded_value_20d(prices_long: pd.DataFrame, symbol: str) -> float:
    """prices_long: Date,Symbol,Close,Volume."""
    df = prices_long[prices_long["Symbol"] == symbol].copy()
    if df.empty or "Volume" not in df.columns or "Close" not in df.columns:
        return float("nan")
    df["TV"] = pd.to_numeric(df["Close"], errors="coerce") * pd.to_numeric(df["Volume"], errors="coerce")
    return float(df["TV"].tail(20).median())


def hl_spread_pct(prices_long: pd.DataFrame, symbol: str) -> float:
    """Median (High-Low)/Close over last 20 sessions — proxy for bid-ask width."""
    df = prices_long[prices_long["Symbol"] == symbol].copy()
    if df.empty or not {"High", "Low", "Close"}.issubset(df.columns):
        return float("nan")
    df["sp"] = (pd.to_numeric(df["High"]) - pd.to_numeric(df["Low"])) / pd.to_numeric(df["Close"])
    return float(df["sp"].tail(20).median())


def inav_premium_z(price_series: pd.Series, nav_series: pd.Series, window: int = 60) -> float:
    p = pd.to_numeric(price_series, errors="coerce")
    n = pd.to_numeric(nav_series, errors="coerce")
    if len(p) < 20 or len(n) < 20:
        return float("nan")
    prem = (p - n) / n
    prem = prem.tail(window).dropna()
    if len(prem) < 10 or prem.std(ddof=0) == 0:
        return float("nan")
    return float((prem.iloc[-1] - prem.mean()) / prem.std(ddof=0))


def passes_microstructure(tv20: float, prem_z: float, liquidity_min: float = LIQUIDITY_MIN_INR,
                          max_prem_z: float = 2.5) -> tuple[bool, list[str]]:
    reasons = []
    if pd.isna(tv20) or tv20 < liquidity_min:
        reasons.append(f"LOW_LIQUIDITY(<{liquidity_min:.0f})")
    if not pd.isna(prem_z) and abs(prem_z) > max_prem_z:
        reasons.append(f"INAV_PREMIUM_Z={prem_z:.2f}")
    return (len(reasons) == 0, reasons)
