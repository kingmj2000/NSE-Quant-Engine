"""
Step 9 — Walk-Forward Backtest & Engine Scorecard.

Replays the recent past by reconstructing a simplified 'engine top-N' at each
historical rebalance date using ONLY data available at time t (no look-ahead),
then marks the picks to market for the chosen hold horizon.

The scoring surrogate used for historical rebalances is intentionally
lightweight — a blend of 21D momentum, 63D momentum and inverse 20D vol —
because we can't reproduce the full live scoring stack for every past date
cheaply. This gives a directional check on whether the *style* the engine
favours (risk-adjusted momentum) has been paying off recently. The result is
labelled 'Style_Backtest' in the CSV so downstream consumers don't confuse it
with a full walk-forward of the live engine.

Pure historical stats. Never raises. Returns empty frames on bad input.
"""
from __future__ import annotations

from typing import Iterable
import numpy as np
import pandas as pd

DEFAULT_LOOKBACK_DAYS = 250
DEFAULT_REBAL_EVERY = 5           # sessions
DEFAULT_HOLD_DAYS = 10
DEFAULT_TOP_N = 5


def _wide(prices_long: pd.DataFrame) -> pd.DataFrame:
    if prices_long is None or prices_long.empty:
        return pd.DataFrame()
    df = prices_long.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"])
    return df.pivot_table(index="Date", columns="Symbol",
                          values="Close", aggfunc="last").sort_index()


def _score_asof(wide: pd.DataFrame, t_idx: int) -> pd.Series:
    """Score the universe using data strictly up to and including t_idx."""
    hist = wide.iloc[: t_idx + 1]
    if len(hist) < 65:
        return pd.Series(dtype=float)
    r21 = hist.iloc[-1] / hist.iloc[-22] - 1.0
    r63 = hist.iloc[-1] / hist.iloc[-64] - 1.0
    daily = hist.pct_change().tail(20)
    vol = daily.std(ddof=0)
    inv_vol = 1.0 / vol.replace(0, np.nan)
    def _rank(s):
        s = s.dropna()
        if s.empty:
            return s
        return (s.rank(pct=True) - 0.5) * 2.0
    z = pd.concat([_rank(r21), _rank(r63), _rank(inv_vol)], axis=1).mean(axis=1)
    return z.dropna()


def _forward_return(wide: pd.DataFrame, sym: str, t_idx: int, h: int) -> float:
    if sym not in wide.columns:
        return np.nan
    if t_idx + h >= len(wide):
        return np.nan
    p0 = wide[sym].iloc[t_idx]
    p1 = wide[sym].iloc[t_idx + h]
    if not (p0 and p1) or pd.isna(p0) or pd.isna(p1):
        return np.nan
    return float(p1 / p0 - 1.0)


def run_backtest(prices_long: pd.DataFrame,
                 lookback_days: int = DEFAULT_LOOKBACK_DAYS,
                 rebal_every: int = DEFAULT_REBAL_EVERY,
                 hold_days: int = DEFAULT_HOLD_DAYS,
                 top_n: int = DEFAULT_TOP_N,
                 benchmark: str = "^NSEI") -> dict:
    """Returns dict with 'scorecard' (per-variant metrics DataFrame) and
    'equity_curve' (DataFrame with per-rebalance mark-to-market)."""
    wide = _wide(prices_long)
    empty = {"scorecard": pd.DataFrame(), "equity_curve": pd.DataFrame()}
    if wide.empty or len(wide) < lookback_days + hold_days + 10:
        return empty

    start = len(wide) - lookback_days
    events = []
    for t in range(start, len(wide) - hold_days, rebal_every):
        z = _score_asof(wide, t)
        # exclude benchmark tickers from picks
        z = z[~z.index.astype(str).str.startswith("^")]
        if z.empty:
            continue
        ranked = z.sort_values(ascending=False)
        picks = ranked.head(top_n).index.tolist()
        top1 = picks[:1]
        rets_top5 = [_forward_return(wide, s, t, hold_days) for s in picks]
        rets_top1 = [_forward_return(wide, s, t, hold_days) for s in top1]
        bench_ret = _forward_return(wide, benchmark, t, hold_days)
        events.append({
            "Rebalance_Date": wide.index[t],
            "Picks_Top5": ",".join(picks),
            "Ret_Top5_EW_%": float(np.nanmean(rets_top5) * 100.0) if rets_top5 else np.nan,
            "Ret_Top1_%": float(rets_top1[0] * 100.0) if rets_top1 and pd.notna(rets_top1[0]) else np.nan,
            "Ret_Bench_%": float(bench_ret * 100.0) if pd.notna(bench_ret) else np.nan,
        })
    if not events:
        return empty
    curve = pd.DataFrame(events)

    def _metrics(col: str, label: str) -> dict:
        s = pd.to_numeric(curve[col], errors="coerce").dropna() / 100.0
        if s.empty:
            return {"Variant": label, "N_Rebalances": 0}
        hit = float((s > 0).mean())
        wins = s[s > 0]; losses = s[s < 0]
        avg_win = float(wins.mean()) if len(wins) else np.nan
        avg_loss = float(losses.mean()) if len(losses) else np.nan
        payoff = float(-avg_win / avg_loss) if avg_loss and avg_loss < 0 else np.nan
        sharpe = float(s.mean() / s.std(ddof=0) * np.sqrt(252 / hold_days)) \
            if s.std(ddof=0) > 0 else np.nan
        down = s[s < 0]
        sortino = float(s.mean() / down.std(ddof=0) * np.sqrt(252 / hold_days)) \
            if len(down) > 1 and down.std(ddof=0) > 0 else np.nan
        equity = (1 + s).cumprod()
        maxdd = float((equity / equity.cummax() - 1).min())
        excess = None
        if "Ret_Bench_%" in curve.columns:
            b = pd.to_numeric(curve["Ret_Bench_%"], errors="coerce") / 100.0
            excess = float((s - b).mean() * (252 / hold_days) * 100.0)
        return {
            "Variant": label,
            "N_Rebalances": int(len(s)),
            "Hit_Rate": round(hit, 3),
            "AvgWin_%": round(avg_win * 100, 3) if pd.notna(avg_win) else np.nan,
            "AvgLoss_%": round(avg_loss * 100, 3) if pd.notna(avg_loss) else np.nan,
            "Payoff_Ratio": round(payoff, 2) if pd.notna(payoff) else np.nan,
            "Sharpe_Ann": round(sharpe, 2) if pd.notna(sharpe) else np.nan,
            "Sortino_Ann": round(sortino, 2) if pd.notna(sortino) else np.nan,
            "MaxDD_%": round(maxdd * 100, 2),
            "AnnExcess_vs_Bench_%": round(excess, 2) if excess is not None else np.nan,
            "Hold_Days": hold_days,
        }

    scorecard = pd.DataFrame([
        _metrics("Ret_Top5_EW_%", "Style_Backtest_Top5_EW"),
        _metrics("Ret_Top1_%",    "Style_Backtest_Top1"),
        _metrics("Ret_Bench_%",   "Benchmark_BuyAndRebal"),
    ])

    # equity curves (compounded per-variant)
    for label, col in [("Top5_EW", "Ret_Top5_EW_%"),
                       ("Top1", "Ret_Top1_%"),
                       ("Benchmark", "Ret_Bench_%")]:
        s = pd.to_numeric(curve[col], errors="coerce").fillna(0) / 100.0
        curve[f"Equity_{label}"] = (1 + s).cumprod()

    return {"scorecard": scorecard, "equity_curve": curve}
