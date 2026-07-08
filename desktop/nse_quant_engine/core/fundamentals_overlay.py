"""
Step 6 — Fundamentals & Quality Overlay.

Reuses cached fundamentals fetched by core.fundamental_factor for the
shortlisted universe (no extra network calls). Produces:

  * quality_score()   → z-score blend in [-3, +3]
  * valuation_flag()  → 'Cheap' / 'Fair' / 'Expensive' vs self-history and peers
  * enrich()          → one row per symbol with all overlay columns

ETFs are bypassed here — they use core.etf_microstructure. The overlay is
report-only by default (QUALITY_WEIGHT = 0.0 in config). Every function is
pure and NaN-safe: missing fundamentals produce NaN, never fabricated values.
"""
from __future__ import annotations

from typing import Optional
import numpy as np
import pandas as pd


# Higher-is-better metrics vs lower-is-better metrics
_POS = ("ROE_TTM", "EPS_Growth_YoY", "EarningsSurprise_Last4Q", "ProfitMargin")
_NEG = ("DebtToEquity", "PromoterPledgePct")


def _z(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() < 3:
        return pd.Series(np.nan, index=s.index)
    mu, sd = s.mean(), s.std(ddof=0)
    if not sd or np.isnan(sd):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sd


def _clip(s: pd.Series, lo: float = -3.0, hi: float = 3.0) -> pd.Series:
    return s.clip(lo, hi)


def quality_score(fund: pd.DataFrame) -> pd.Series:
    """z-score blend in [-3, +3]. Positive = higher quality vs peers.

    Uses the columns present in `fund`; each contributes with equal weight
    after individual winsorised z-scoring. Sign is flipped for _NEG metrics.
    Returns NaN for rows with no usable fields.
    """
    if fund is None or fund.empty:
        return pd.Series(dtype=float)
    parts = []
    for c in _POS:
        if c in fund.columns:
            parts.append(_z(fund[c]))
    for c in _NEG:
        if c in fund.columns:
            parts.append(-_z(fund[c]))
    if not parts:
        return pd.Series(np.nan, index=fund.index)
    mat = pd.concat(parts, axis=1)
    # require ≥2 non-NaN inputs for a meaningful blend
    mask = mat.notna().sum(axis=1) >= 2
    out = mat.mean(axis=1, skipna=True)
    out[~mask] = np.nan
    return _clip(out)


def valuation_flag(pe: float,
                   self_median_pe: Optional[float],
                   sector_median_pe: Optional[float]) -> str:
    """Cheap / Fair / Expensive vs 3Y self-median and sector median."""
    if pd.isna(pe) or pe is None or pe <= 0:
        return "Unknown"
    votes = 0
    denom = 0
    for ref in (self_median_pe, sector_median_pe):
        if ref is None or pd.isna(ref) or ref <= 0:
            continue
        denom += 1
        if pe < ref * 0.85:
            votes += 1        # cheap
        elif pe > ref * 1.15:
            votes -= 1        # expensive
    if denom == 0:
        return "Unknown"
    if votes >= 1:
        return "Cheap"
    if votes <= -1:
        return "Expensive"
    return "Fair"


def enrich(top5: pd.DataFrame,
           fund: pd.DataFrame,
           sector_median_pe: Optional[pd.Series] = None) -> pd.DataFrame:
    """Given the top-5 slice and a cached fundamentals frame (Symbol + fields),
    return a merged frame with Quality_Score, Valuation_Flag, and coverage.

    - `fund` may include: ROE_TTM, DebtToEquity, EPS_Growth_YoY, PE_TTM, PEG,
      EarningsSurprise_Last4Q, PromoterPledgePct, ProfitMargin, PE_Self_Median_3Y
    - Missing symbols/columns → NaN. Never raises.
    """
    if top5 is None or top5.empty:
        return pd.DataFrame()
    if fund is None or fund.empty:
        out = top5[["Symbol"]].copy()
        out["Quality_Score"] = np.nan
        out["Valuation_Flag"] = "Unknown"
        out["Fundamentals_Coverage"] = 0.0
        return out

    q = quality_score(fund)
    fund = fund.copy()
    fund["Quality_Score"] = q

    all_cols = list(_POS) + list(_NEG) + ["PE_TTM", "PEG"]
    present = [c for c in all_cols if c in fund.columns]
    if present:
        fund["Fundamentals_Coverage"] = (
            fund[present].notna().sum(axis=1) / max(len(present), 1)
        )
    else:
        fund["Fundamentals_Coverage"] = 0.0

    smap = {}
    if sector_median_pe is not None and len(sector_median_pe):
        smap = {str(k): float(v) for k, v in sector_median_pe.items() if pd.notna(v)}

    def _row_flag(r):
        pe = pd.to_numeric(r.get("PE_TTM", np.nan), errors="coerce")
        self_med = pd.to_numeric(r.get("PE_Self_Median_3Y", np.nan), errors="coerce")
        sec = smap.get(str(r.get("Sector", "")), np.nan) if smap else np.nan
        return valuation_flag(pe, self_med if pd.notna(self_med) else None,
                              sec if pd.notna(sec) else None)

    fund["Valuation_Flag"] = fund.apply(_row_flag, axis=1)

    keep = ["Symbol", "Quality_Score", "Valuation_Flag", "Fundamentals_Coverage",
            "PE_TTM", "PEG", "ROE_TTM", "DebtToEquity", "EPS_Growth_YoY"]
    keep = [c for c in keep if c in fund.columns]
    return top5[["Symbol"]].merge(fund[keep], on="Symbol", how="left")
