"""
Correlation-aware top-N selection + per-candidate benchmark stats.

Motivation (from the step 2 plan): today's top-5 is a pure Final_Score sort,
which can concentrate five names in a single factor (e.g. all IT). This module
diversifies the final pick by penalising pairwise correlation, and computes
per-candidate alpha/IR/tracking-error against Nifty 50 so the dashboard can
show whether each candidate is really adding something over the index.

Pure functions — no I/O, no globals. Callers pass the long-form prices frame
that nse_quant_engine.py already writes to data/raw_prices_latest.csv.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BENCHMARK_SYMBOL_DEFAULT = "^NSEI"


# ── returns panel ───────────────────────────────────────────────────────────

def _returns_panel(prices_long: pd.DataFrame, symbols: list[str],
                   window: int) -> pd.DataFrame:
    """Return a wide DataFrame of daily pct-change returns for `symbols`,
    limited to the last `window` sessions. Columns are symbols; missing
    symbols are silently dropped."""
    if prices_long is None or prices_long.empty:
        return pd.DataFrame()
    df = prices_long[prices_long["Symbol"].isin(symbols)].copy()
    if df.empty:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"])
    wide = (
        df.pivot_table(index="Date", columns="Symbol", values="Close", aggfunc="last")
          .sort_index()
    )
    rets = wide.pct_change().tail(window).dropna(how="all")
    return rets


def pairwise_corr(prices_long: pd.DataFrame, symbols: list[str],
                  window: int = 60) -> pd.DataFrame:
    """Pairwise return correlation over the last `window` sessions.
    Symbols with insufficient history are dropped from the output matrix."""
    rets = _returns_panel(prices_long, symbols, window)
    if rets.empty or rets.shape[1] < 2:
        return pd.DataFrame()
    # require at least window/2 observations to include a symbol
    good = rets.columns[rets.notna().sum() >= max(10, window // 2)].tolist()
    if len(good) < 2:
        return pd.DataFrame()
    return rets[good].corr().fillna(0.0)


# ── greedy diversified selection ─────────────────────────────────────────────

def diversified_top_n(candidates: pd.DataFrame,
                      corr: pd.DataFrame,
                      n: int = 5,
                      alpha: float = 0.65,
                      score_col: str = "Confidence_Adjusted_Score",
                      symbol_col: str = "Symbol") -> list[str]:
    """Greedy pick: start with the top-scored symbol, then iteratively add the
    candidate maximising ``alpha·norm_score − (1-alpha)·max_corr_with_selected``.

    If corr is missing / empty, or fewer than n candidates share a corr row,
    fall back to a plain top-N by score.
    """
    if candidates is None or candidates.empty:
        return []
    ranked = candidates.sort_values(score_col, ascending=False).reset_index(drop=True)
    all_syms = ranked[symbol_col].astype(str).tolist()
    if corr is None or corr.empty:
        return all_syms[:n]

    corr_syms = set(corr.index.astype(str))
    pool = [s for s in all_syms if s in corr_syms]
    if len(pool) < 2:
        return all_syms[:n]

    scores = dict(zip(ranked[symbol_col].astype(str),
                      pd.to_numeric(ranked[score_col], errors="coerce").fillna(0.0)))
    smax = max(scores.values()) or 1.0
    norm = {s: (scores[s] / smax) for s in pool}

    selected: list[str] = [pool[0]]
    remaining = [s for s in pool if s != pool[0]]
    while remaining and len(selected) < n:
        best_sym, best_val = None, -1e18
        for s in remaining:
            max_c = max(abs(float(corr.loc[s, t])) for t in selected)
            val = alpha * norm[s] - (1.0 - alpha) * max_c
            if val > best_val:
                best_val, best_sym = val, s
        selected.append(best_sym)
        remaining.remove(best_sym)

    # if the pool ran out before n, top up with the highest-scoring leftovers
    if len(selected) < n:
        for s in all_syms:
            if s not in selected:
                selected.append(s)
                if len(selected) >= n:
                    break
    return selected


# ── benchmark / IR stats ─────────────────────────────────────────────────────

_TRADING_DAYS = 252


def benchmark_stats(prices_long: pd.DataFrame,
                    symbols: list[str],
                    benchmark: str = BENCHMARK_SYMBOL_DEFAULT,
                    short_window: int = 21,
                    long_window: int = 63) -> pd.DataFrame:
    """Per-symbol alpha/IR/tracking-error/beta vs benchmark. Returns one row
    per input symbol; missing data → NaN row (never raises)."""
    cols = ["Symbol", "Excess_21D", "InformationRatio_63D",
            "TrackingError_63D", "BetaVsBenchmark_63D"]
    if prices_long is None or prices_long.empty or not symbols:
        return pd.DataFrame(columns=cols)

    wanted = list({*symbols, benchmark})
    rets = _returns_panel(prices_long, wanted, window=long_window + 5)
    if rets.empty or benchmark not in rets.columns:
        return pd.DataFrame([{"Symbol": s, **{c: np.nan for c in cols[1:]}} for s in symbols])

    bench = rets[benchmark]
    rows = []
    for s in symbols:
        rec = {"Symbol": s, "Excess_21D": np.nan, "InformationRatio_63D": np.nan,
               "TrackingError_63D": np.nan, "BetaVsBenchmark_63D": np.nan}
        if s not in rets.columns:
            rows.append(rec); continue
        stk = rets[s]
        try:
            s21 = stk.tail(short_window).dropna()
            b21 = bench.tail(short_window).dropna()
            if len(s21) >= 5 and len(b21) >= 5:
                r_s = (1.0 + s21).prod() - 1.0
                r_b = (1.0 + b21).prod() - 1.0
                rec["Excess_21D"] = float(r_s - r_b)

            paired = pd.concat([stk, bench], axis=1, keys=["s", "b"]).dropna().tail(long_window)
            if len(paired) >= 20:
                excess = paired["s"] - paired["b"]
                sd = excess.std(ddof=0)
                if sd and not np.isnan(sd):
                    rec["TrackingError_63D"] = float(sd * np.sqrt(_TRADING_DAYS))
                    rec["InformationRatio_63D"] = float(excess.mean() / sd * np.sqrt(_TRADING_DAYS))
                var_b = paired["b"].var(ddof=0)
                if var_b and not np.isnan(var_b):
                    rec["BetaVsBenchmark_63D"] = float(paired.cov().loc["s", "b"] / var_b)
        except Exception:
            pass
        rows.append(rec)
    return pd.DataFrame(rows, columns=cols)


def avg_abs_offdiag(corr: pd.DataFrame) -> float:
    """Mean of absolute off-diagonal correlations — a single 'diversification'
    number for a portfolio (lower = more diversified)."""
    if corr is None or corr.empty or corr.shape[0] < 2:
        return float("nan")
    a = corr.abs().values.copy()
    np.fill_diagonal(a, np.nan)
    return float(np.nanmean(a))
