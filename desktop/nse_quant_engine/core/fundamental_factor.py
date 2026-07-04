"""
Fundamental / quality factor (clean core v4) — NEW analysis piece.

What it does: turns basic fundamentals into a 0..100 quality score per stock,
which scoring.apply_fundamental_factor folds into the final score at a LOW
default weight (config.FUNDAMENTAL_WEIGHT, default 0.15).

Honest caveats — read these:
  * This adds a *new data dependency*: per-stock fundamentals. yfinance exposes
    some via Ticker.info (trailingPE, returnOnEquity, debtToEquity,
    earningsGrowth, profitMargins) but coverage for NSE names is patchy and the
    fetch is the ONE part of this module I could not test against your live feed.
  * ETFs have no meaningful single-name fundamentals -> they are skipped
    (neutral), controlled by config.FUNDAMENTAL_APPLIES_TO_ETF.
  * Keep the weight low until your cross-sectional validation shows the factor
    actually improves the top-minus-bottom spread. It may not. That's fine —
    the validation layer exists precisely to answer that.

The SCORING logic (build_quality_score, percentile blending) is pure and tested.
The FETCH (fetch_fundamentals) is a thin wrapper you should sanity-check on a few
symbols before trusting at scale.
"""

from __future__ import annotations
import numpy as np
import pandas as pd


# ── fetch (thin wrapper, live-data-dependent, verify before trusting) ───────
def fetch_fundamentals(symbols: list[str], sleep: float = 0.0) -> pd.DataFrame:
    """
    Pull basic fundamentals via yfinance. Returns columns:
        Symbol, PE, ROE, DebtToEquity, EarningsGrowth, ProfitMargin
    Missing fields come back as NaN. Never raises on a single bad symbol.

    NOTE: unverified against live NSE data in this environment. Run on ~5
    symbols first and eyeball the numbers before a full-universe run.
    """
    import time
    try:
        import yfinance as yf
    except Exception:
        return pd.DataFrame(columns=["Symbol", "PE", "ROE", "DebtToEquity",
                                     "EarningsGrowth", "ProfitMargin"])
    rows = []
    for sym in symbols:
        rec = {"Symbol": sym, "PE": np.nan, "ROE": np.nan,
               "DebtToEquity": np.nan, "EarningsGrowth": np.nan, "ProfitMargin": np.nan}
        try:
            info = yf.Ticker(sym).info or {}
            rec["PE"] = info.get("trailingPE", np.nan)
            rec["ROE"] = info.get("returnOnEquity", np.nan)
            rec["DebtToEquity"] = info.get("debtToEquity", np.nan)
            rec["EarningsGrowth"] = info.get("earningsGrowth", np.nan)
            rec["ProfitMargin"] = info.get("profitMargins", np.nan)
        except Exception:
            pass
        rows.append(rec)
        if sleep:
            time.sleep(sleep)
    return pd.DataFrame(rows)


# ── scoring (pure, tested) ───────────────────────────────────────────────────
def _winsorize(s: pd.Series, lo=0.02, hi=0.98) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() < 5:
        return s
    ql, qh = s.quantile(lo), s.quantile(hi)
    return s.clip(ql, qh)


def build_quality_score(fund: pd.DataFrame) -> pd.DataFrame:
    """
    Turn raw fundamentals into a 0..100 Fundamental_Score via percentile blending.
    Direction handled per metric:
        ROE up = good, EarningsGrowth up = good, ProfitMargin up = good,
        PE lower = better (cheaper), DebtToEquity lower = better (safer).
    Weights are deliberately simple and equal-ish; tune later if validated.
    """
    out = fund.copy()
    for col in ["PE", "ROE", "DebtToEquity", "EarningsGrowth", "ProfitMargin"]:
        if col not in out.columns:
            out[col] = np.nan

    roe   = _winsorize(out["ROE"]).rank(pct=True) * 100
    egr   = _winsorize(out["EarningsGrowth"]).rank(pct=True) * 100
    marg  = _winsorize(out["ProfitMargin"]).rank(pct=True) * 100
    # invert: lower is better
    pe    = (1 - _winsorize(out["PE"]).rank(pct=True)) * 100
    dte   = (1 - _winsorize(out["DebtToEquity"]).rank(pct=True)) * 100

    weights = {"roe": 0.25, "egr": 0.20, "marg": 0.20, "pe": 0.20, "dte": 0.15}
    comps = pd.DataFrame({"roe": roe, "egr": egr, "marg": marg, "pe": pe, "dte": dte})

    # Weighted mean over only the components that exist for each row, so a stock
    # missing one metric isn't unfairly zeroed.
    scores = []
    for _, r in comps.iterrows():
        num = den = 0.0
        for k, w in weights.items():
            if pd.notna(r[k]):
                num += r[k] * w
                den += w
        scores.append(num / den if den > 0 else np.nan)
    out["Fundamental_Score"] = scores
    out["Fundamental_Coverage"] = comps.notna().sum(axis=1) / len(weights)
    return out[["Symbol", "Fundamental_Score", "Fundamental_Coverage",
                "PE", "ROE", "DebtToEquity", "EarningsGrowth", "ProfitMargin"]]
