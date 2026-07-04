"""
Scoring engine (clean core v4).

Fixes the central analytical flaw of earlier versions: momentum was counted
THREE times (raw momentum + relative strength + trend), all of which are
functions of recent return, so the score was ~65% "it went up lately."

New design — momentum is the single primary signal; everything else modifies it:

    1. risk_adjusted_momentum = blended multi-horizon return / volatility
       (a Sharpe-like quantity; rewards return EARNED PER UNIT of risk, so a
        calm 6% beats a violent 9%)
    2. percentile-rank that across the universe        -> base 0..100
    3. apply TREND as a soft confirmation multiplier   (not an additive third)
    4. apply RELATIVE STRENGTH as a soft multiplier     (not an additive third)
    5. subtract risk penalties (vol, drawdown, overbought)
    6. apply absolute filters (cap score if 21D return <= 0, or 5D crash)
    7. fold in the optional fundamental/quality factor at a low weight

Pure functions, no I/O — fully unit-testable with synthetic data.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from . import config as C


def blended_momentum(r5: float, r21: float, r63: float) -> float:
    parts, weights = [], []
    for r, w in [(r5, C.MOM_W_5D), (r21, C.MOM_W_21D), (r63, C.MOM_W_63D)]:
        if pd.notna(r):
            parts.append(r * w)
            weights.append(w)
    if not weights:
        return np.nan
    return sum(parts) / sum(weights)


def risk_adjusted_momentum(mom: float, vol_20d: float) -> float:
    """Momentum per unit of volatility. Guards against divide-by-zero / stale-price
    near-zero vol (which previously made dead instruments look 'safe')."""
    if pd.isna(mom):
        return np.nan
    v = vol_20d if (pd.notna(vol_20d) and vol_20d > 0.02) else 0.02
    return mom / v


def percentile_rank(series: pd.Series) -> pd.Series:
    """0..100 percentile rank, NaN-safe."""
    s = pd.to_numeric(series, errors="coerce")
    return s.rank(pct=True) * 100.0


def trend_multiplier(price: float, ma50: float, ma200: float) -> float:
    if pd.isna(price) or pd.isna(ma50):
        return C.TREND_FAIL_MULT
    above50 = price >= ma50
    above200 = pd.isna(ma200) or price >= ma200
    return C.TREND_CONFIRM_MULT if (above50 and above200) else C.TREND_FAIL_MULT


def rs_multiplier(ret_21d: float, bench_21d: float) -> float:
    if pd.isna(ret_21d) or pd.isna(bench_21d):
        return C.RS_FAIL_MULT
    return C.RS_CONFIRM_MULT if ret_21d >= bench_21d else C.RS_FAIL_MULT


def risk_penalty(vol_20d: float, drawdown_60d: float, rsi: float,
                 vol_pctile: float) -> float:
    pen = 0.0
    if pd.notna(vol_pctile):
        pen += (vol_pctile / 100.0) * C.VOL_PENALTY_MAX
    if pd.notna(drawdown_60d):
        pen += min(abs(drawdown_60d) * 100.0, 1.0) * C.DRAWDOWN_PENALTY_MAX * 0.5
    if pd.notna(rsi) and rsi >= C.OVERBOUGHT_RSI:
        pen += C.OVERBOUGHT_PENALTY
    return pen


def absolute_filter_cap(ret_21d: float, ret_5d: float) -> float | None:
    """Return a hard score ceiling if the name fails absolute momentum, else None."""
    if pd.isna(ret_21d) or ret_21d <= C.MIN_ABS_RETURN_21D:
        return 49.9
    if pd.notna(ret_5d) and ret_5d <= C.MAX_ABS_DROP_5D:
        return 59.9
    return None


def compute_opportunity_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expects columns: Symbol, Return_5D, Return_21D, Return_63D, Volatility_20D,
    Price, MA50, MA200, Bench_Return_21D, Drawdown_60D, RSI.
    Optionally: Fundamental_Score (0..100), Universe.
    Returns df with Opportunity_Score and component columns added.
    """
    out = df.copy()

    out["Blended_Momentum"] = out.apply(
        lambda r: blended_momentum(r.get("Return_5D"), r.get("Return_21D"), r.get("Return_63D")),
        axis=1,
    )
    out["Risk_Adj_Momentum"] = out.apply(
        lambda r: risk_adjusted_momentum(r["Blended_Momentum"], r.get("Volatility_20D")),
        axis=1,
    )
    out["Momentum_Pctile"] = percentile_rank(out["Risk_Adj_Momentum"])
    out["Vol_Pctile"] = percentile_rank(out.get("Volatility_20D", pd.Series(index=out.index)))

    scores = []
    for _, r in out.iterrows():
        base = r["Momentum_Pctile"]
        if pd.isna(base):
            scores.append(np.nan)
            continue
        base *= trend_multiplier(r.get("Price"), r.get("MA50"), r.get("MA200"))
        base *= rs_multiplier(r.get("Return_21D"), r.get("Bench_Return_21D"))
        base -= risk_penalty(r.get("Volatility_20D"), r.get("Drawdown_60D"),
                             r.get("RSI"), r.get("Vol_Pctile"))
        base = max(0.0, min(100.0, base))

        cap = absolute_filter_cap(r.get("Return_21D"), r.get("Return_5D"))
        if cap is not None:
            base = min(base, cap)
        scores.append(base)
    out["Opportunity_Score"] = scores
    return out


def apply_fundamental_factor(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fold the optional fundamental/quality score into the final score.
    Final = (1-w) * Opportunity_Score + w * Fundamental_Score, with w=0 disabling.
    ETFs (no fundamentals) keep their Opportunity_Score unchanged.
    """
    out = df.copy()
    w = C.FUNDAMENTAL_WEIGHT
    if w <= 0 or "Fundamental_Score" not in out.columns:
        out["Final_Score"] = out["Opportunity_Score"]
        return out

    finals = []
    for _, r in out.iterrows():
        opp = r.get("Opportunity_Score")
        fund = r.get("Fundamental_Score")
        is_etf = str(r.get("Universe", "")).lower() == "etf"
        if pd.isna(opp):
            finals.append(np.nan)
        elif is_etf and not C.FUNDAMENTAL_APPLIES_TO_ETF:
            finals.append(opp)
        elif pd.isna(fund):
            finals.append(opp)  # missing fundamentals -> no adjustment, no penalty
        elif "Fundamental_Coverage" in out.columns and pd.notna(r.get("Fundamental_Coverage")) and float(r.get("Fundamental_Coverage")) < C.FUNDAMENTAL_MIN_COVERAGE:
            finals.append(opp)  # too few fundamental fields -> avoid false precision
        else:
            finals.append((1 - w) * opp + w * fund)
    out["Final_Score"] = finals
    return out
