"""
Alpha Zoo v0 — price/volume-only formulaic alphas (inspired by HKUDS/Vibe-Trading
Alpha Zoo; original design and code here — no copied code, no new dependencies).

Every alpha is a pure function of a per-symbol OHLCV frame indexed by date, and
returns a single scalar "as-of-today" signal. Higher = more attractive by
convention (functions that are naturally "lower is better" are sign-flipped
here so the zoo blends monotonically).

Discipline (matches the Vibe-Trading lookahead-guard idea):
  * NEVER use .shift(-N) or negative rolling windows.
  * NEVER reference a future-dated index.
  * All rolling stats use `min_periods` so short histories return NaN, not junk.

Wiring (deliberate, minimal — full blend is a separate delivery step):
  * `compute_alpha_zoo(prices_long)` returns a DataFrame indexed by Symbol with
    one column per alpha plus a `Zoo_Score` (0..100 cross-sectional composite
    of equally-weighted percentile ranks over the alphas the row has values for).
  * Consumers can merge `Zoo_Score` onto `latest_scores` and blend at whatever
    weight cross-sectional validation later justifies.

Nothing here is enabled by default — scoring.py is unchanged.
"""
from __future__ import annotations

from typing import Callable
import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# individual alphas — each takes a per-symbol OHLCV frame sorted by Date asc
# ─────────────────────────────────────────────────────────────────────────────

def _c(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df.get(col), errors="coerce")


def alpha_mom_12_1(df: pd.DataFrame) -> float:
    """Classic 12-month momentum skipping the most recent month (~21 sessions).
    Return over t-252..t-21. Positive is bullish."""
    c = _c(df, "Close").dropna()
    if len(c) < 252:
        return np.nan
    return float(c.iloc[-21] / c.iloc[-252] - 1.0)


def alpha_52w_high_proximity(df: pd.DataFrame) -> float:
    """1 - (52w high - close)/52w high. Range 0..1; closer to 1 = near high."""
    c = _c(df, "Close").dropna()
    if len(c) < 60:
        return np.nan
    hi = c.tail(252).max()
    return float(1.0 - (hi - c.iloc[-1]) / hi) if hi > 0 else np.nan


def alpha_amihud_illiquidity_inv(df: pd.DataFrame) -> float:
    """Inverse Amihud: -mean(|ret|/traded_value). Higher = MORE liquid = better."""
    c = _c(df, "Close"); v = _c(df, "Volume")
    if c.dropna().shape[0] < 25 or v.dropna().shape[0] < 25:
        return np.nan
    ret = c.pct_change().abs()
    tv = c * v
    x = (ret / tv.replace(0, np.nan)).tail(20).dropna()
    if x.empty:
        return np.nan
    return float(-x.mean())


def alpha_obv_slope(df: pd.DataFrame) -> float:
    """Slope of on-balance volume over last 20 sessions, normalized by mean OBV."""
    c = _c(df, "Close"); v = _c(df, "Volume")
    if c.dropna().shape[0] < 25 or v.dropna().shape[0] < 25:
        return np.nan
    sign = np.sign(c.diff().fillna(0.0))
    obv = (sign * v.fillna(0.0)).cumsum()
    tail = obv.tail(20)
    if tail.isna().any() or len(tail) < 20:
        return np.nan
    x = np.arange(len(tail), dtype=float)
    slope = np.polyfit(x, tail.values, 1)[0]
    denom = max(abs(tail.mean()), 1.0)
    return float(slope / denom)


def alpha_chaikin_money_flow(df: pd.DataFrame, window: int = 20) -> float:
    """Chaikin Money Flow: sum(MFV) / sum(volume) over N sessions."""
    h = _c(df, "High"); l = _c(df, "Low"); c = _c(df, "Close"); v = _c(df, "Volume")
    if min(h.dropna().shape[0], l.dropna().shape[0], c.dropna().shape[0]) < window + 1:
        return np.nan
    hl = (h - l).replace(0, np.nan)
    mfm = ((c - l) - (h - c)) / hl
    mfv = mfm * v
    num = mfv.tail(window).sum()
    den = v.tail(window).sum()
    return float(num / den) if den else np.nan


def alpha_mean_reversion_5d_z(df: pd.DataFrame) -> float:
    """Negative 5-day z-score of close vs 20D mean — high when oversold."""
    c = _c(df, "Close").dropna()
    if len(c) < 25:
        return np.nan
    m = c.tail(20).mean(); s = c.tail(20).std(ddof=0)
    if not s or np.isnan(s):
        return np.nan
    z5 = (c.iloc[-1] - m) / s
    return float(-z5)


def alpha_vol_adj_breakout(df: pd.DataFrame) -> float:
    """(Close - 20D high shift 1) / 20D ATR-proxy — positive = fresh breakout."""
    c = _c(df, "Close"); h = _c(df, "High"); l = _c(df, "Low")
    if c.dropna().shape[0] < 25:
        return np.nan
    prior_hi = c.shift(1).rolling(20, min_periods=20).max().iloc[-1]
    tr = (h - l).abs()
    atr = tr.rolling(20, min_periods=20).mean().iloc[-1]
    if not atr or np.isnan(atr) or np.isnan(prior_hi):
        return np.nan
    return float((c.iloc[-1] - prior_hi) / atr)


def alpha_gap_fill(df: pd.DataFrame) -> float:
    """Negative of median overnight gap over 20 sessions — persistent up-gaps score high."""
    o = _c(df, "Open"); c = _c(df, "Close")
    if o.dropna().shape[0] < 25 or c.dropna().shape[0] < 25:
        return np.nan
    gap = (o - c.shift(1)) / c.shift(1)
    g = gap.tail(20).dropna()
    if g.empty:
        return np.nan
    return float(g.median())


def alpha_vol_term_structure(df: pd.DataFrame) -> float:
    """20D vol / 60D vol — <1 means recent calm relative to long-run; bullish (return -x)."""
    c = _c(df, "Close").pct_change()
    if c.dropna().shape[0] < 65:
        return np.nan
    v20 = c.tail(20).std(ddof=0)
    v60 = c.tail(60).std(ddof=0)
    if not v60 or np.isnan(v60):
        return np.nan
    return float(-(v20 / v60))


def alpha_low_vol_anomaly(df: pd.DataFrame) -> float:
    """Negative 60D realized vol — low-vol premium proxy."""
    c = _c(df, "Close").pct_change().dropna()
    if len(c) < 60:
        return np.nan
    return float(-c.tail(60).std(ddof=0))


def alpha_drawdown_recovery(df: pd.DataFrame) -> float:
    """1 - (peak-close)/peak over last 60 sessions; 1.0 = at new high."""
    c = _c(df, "Close").dropna()
    if len(c) < 60:
        return np.nan
    peak = c.tail(60).cummax().iloc[-1]
    return float(1.0 - (peak - c.iloc[-1]) / peak) if peak > 0 else np.nan


def alpha_up_day_ratio(df: pd.DataFrame) -> float:
    """Share of positive days over last 21 sessions."""
    c = _c(df, "Close").pct_change().dropna().tail(21)
    if len(c) < 15:
        return np.nan
    return float((c > 0).mean())


def alpha_quiet_uptrend(df: pd.DataFrame) -> float:
    """20D slope of close / 20D std of close — high = smooth uptrend."""
    c = _c(df, "Close").dropna().tail(20)
    if len(c) < 20:
        return np.nan
    x = np.arange(20, dtype=float)
    slope = np.polyfit(x, c.values, 1)[0]
    s = c.std(ddof=0)
    if not s or np.isnan(s):
        return np.nan
    return float(slope / s)


def alpha_volume_surge(df: pd.DataFrame) -> float:
    """5D avg volume / 60D avg volume — >1 means accumulation."""
    v = _c(df, "Volume").dropna()
    if len(v) < 60:
        return np.nan
    v5 = v.tail(5).mean(); v60 = v.tail(60).mean()
    if not v60:
        return np.nan
    return float(v5 / v60)


def alpha_close_over_ma50(df: pd.DataFrame) -> float:
    """Close / 50-DMA - 1."""
    c = _c(df, "Close").dropna()
    if len(c) < 50:
        return np.nan
    ma = c.tail(50).mean()
    return float(c.iloc[-1] / ma - 1.0) if ma else np.nan


# ─────────────────────────────────────────────────────────────────────────────
# Overlay alphas — cached time series keyed by (Date, Symbol). Loaded lazily
# from data/*.csv the first time they're needed. Missing files => NaN alphas
# (safe: evaluator will just not promote them). See core/optional_data_fetchers.
# ─────────────────────────────────────────────────────────────────────────────
_OVERLAY_CACHE: dict[str, pd.DataFrame] = {}


def _overlay_wide(name: str, value_col: str, base_dir: pd.Series | None = None) -> pd.DataFrame:
    """Return wide-format overlay (index=Date, columns=Symbol, values=value_col).
    Cached in-process."""
    if name in _OVERLAY_CACHE:
        return _OVERLAY_CACHE[name]
    try:
        from pathlib import Path as _P
        candidates = [
            _P(__file__).resolve().parent.parent / "data" / f"{name}.csv",
        ]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            _OVERLAY_CACHE[name] = pd.DataFrame()
            return _OVERLAY_CACHE[name]
        df = pd.read_csv(path)
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date", "Symbol", value_col])
        wide = df.pivot_table(index="Date", columns="Symbol",
                              values=value_col, aggfunc="last").sort_index()
        _OVERLAY_CACHE[name] = wide
        return wide
    except Exception:
        _OVERLAY_CACHE[name] = pd.DataFrame()
        return _OVERLAY_CACHE[name]


def _overlay_value_asof(name: str, value_col: str, symbol: str,
                        asof: pd.Timestamp | None = None) -> float:
    wide = _overlay_wide(name, value_col)
    if wide.empty or symbol not in wide.columns:
        return np.nan
    ser = wide[symbol].dropna()
    if ser.empty:
        return np.nan
    if asof is not None:
        ser = ser[ser.index <= asof]
        if ser.empty:
            return np.nan
    return float(ser.iloc[-1])


def alpha_delivery_momentum(df: pd.DataFrame) -> float:
    """5d mean delivery% minus 20d mean delivery%.  Reads cached NSE bhavcopy."""
    sym = str(df["Symbol"].iloc[-1]) if "Symbol" in df.columns and not df.empty else None
    if not sym:
        return np.nan
    wide = _overlay_wide("delivery_pct_daily", "Delivery_Pct")
    if wide.empty or sym not in wide.columns:
        return np.nan
    ser = wide[sym].dropna()
    if len(ser) < 20:
        return np.nan
    return float(ser.tail(5).mean() - ser.tail(20).mean())


def alpha_iv_rank(df: pd.DataFrame) -> float:
    """Latest IV_Rank from cached NSE option-chain daily snapshot."""
    sym = str(df["Symbol"].iloc[-1]) if "Symbol" in df.columns and not df.empty else None
    if not sym:
        return np.nan
    return _overlay_value_asof("iv_rank_daily", "IV_Rank", sym)


# ─────────────────────────────────────────────────────────────────────────────
# registry + top-level compute
# ─────────────────────────────────────────────────────────────────────────────

ALPHAS: dict[str, Callable[[pd.DataFrame], float]] = {
    "mom_12_1":            alpha_mom_12_1,
    "high_52w_proximity":  alpha_52w_high_proximity,
    "amihud_liquidity":    alpha_amihud_illiquidity_inv,
    "obv_slope":           alpha_obv_slope,
    "chaikin_mf":          alpha_chaikin_money_flow,
    "mean_rev_5d_z":       alpha_mean_reversion_5d_z,
    "vol_adj_breakout":    alpha_vol_adj_breakout,
    "gap_fill":            alpha_gap_fill,
    "vol_term_structure":  alpha_vol_term_structure,
    "low_vol_anomaly":     alpha_low_vol_anomaly,
    "drawdown_recovery":   alpha_drawdown_recovery,
    "up_day_ratio":        alpha_up_day_ratio,
    "quiet_uptrend":       alpha_quiet_uptrend,
    "volume_surge":        alpha_volume_surge,
    "close_over_ma50":     alpha_close_over_ma50,
    # Candidate overlay alphas — gated by alpha_evaluator + residual-IC check.
    "delivery_momentum":   alpha_delivery_momentum,
    "iv_rank":             alpha_iv_rank,
}


def compute_alpha_zoo(prices_long: pd.DataFrame,
                      symbols: list[str] | None = None) -> pd.DataFrame:
    """Compute all alphas for each symbol.

    prices_long: Date, Symbol, Open, High, Low, Close, Volume (long format).
    Returns DataFrame keyed by Symbol with one column per alpha and a
    cross-sectional composite `Zoo_Score` in 0..100.
    """
    if prices_long is None or prices_long.empty:
        return pd.DataFrame(columns=["Symbol"] + list(ALPHAS.keys()) + ["Zoo_Score"])
    df = prices_long.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Symbol"]).sort_values(["Symbol", "Date"])
    syms = symbols or df["Symbol"].dropna().unique().tolist()
    rows = []
    for s in syms:
        sub = df[df["Symbol"] == s]
        rec = {"Symbol": s}
        for name, fn in ALPHAS.items():
            try:
                rec[name] = fn(sub)
            except Exception:
                rec[name] = np.nan
        rows.append(rec)
    out = pd.DataFrame(rows)
    # cross-sectional percentile rank per alpha; average available ranks per row
    ranks = pd.DataFrame(index=out.index)
    for name in ALPHAS.keys():
        ranks[name] = pd.to_numeric(out[name], errors="coerce").rank(pct=True) * 100.0
    with np.errstate(all="ignore"):
        out["Zoo_Score"] = ranks.mean(axis=1, skipna=True)
        out["Zoo_Coverage"] = ranks.notna().sum(axis=1) / max(len(ALPHAS), 1)
    return out
