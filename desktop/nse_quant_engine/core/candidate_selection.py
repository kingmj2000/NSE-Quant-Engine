"""Canonical candidate ordering & official Top-N helper.

Read-only helpers used by the native UI (Decision Center, Candidates Workbench,
Trade Plan view). This module never mutates the input DataFrame and never
recomputes scores.

Authorities (fixed by product spec):
- Eligibility authority: ``Opportunity_Eligible`` == "Yes"
- Rank authority:        ``Opportunity_Rank`` (positive numeric, ascending)
- Tiebreaker:            ``Confidence_Adjusted_Score`` descending, then Symbol asc

``Final_Score`` MUST NOT be used for sorting or tiebreaking anywhere.

Rows with missing / invalid Opportunity_Rank are placed AFTER validly ranked
rows (still sorted by CAS desc, Symbol asc) so that Risky / Avoid / ineligible
names remain visible for inspection.
"""
from __future__ import annotations

import pandas as pd
import numpy as np


ELIGIBLE_COL = "Opportunity_Eligible"
RANK_COL = "Opportunity_Rank"
PRIMARY_SCORE_COL = "Confidence_Adjusted_Score"
SECONDARY_SCORE_COL = "Final_Score"  # display-only; never used for sorting
SYMBOL_COL = "Symbol"


def is_eligible(df: pd.DataFrame) -> pd.Series:
    """Boolean mask of officially eligible rows (Opportunity_Eligible == 'Yes').

    Missing column → all False (conservative). Case-insensitive string compare.
    """
    if df is None or df.empty or ELIGIBLE_COL not in df.columns:
        return pd.Series([False] * (0 if df is None else len(df)), index=(None if df is None else df.index))
    return df[ELIGIBLE_COL].astype(str).str.strip().str.lower().eq("yes")


def _valid_rank(series: pd.Series) -> pd.Series:
    """Coerce Opportunity_Rank to numeric; treat non-positive/NaN as invalid."""
    s = pd.to_numeric(series, errors="coerce")
    return s.where(s > 0)


def canonical_order(df: pd.DataFrame, *, eligible_only: bool = False) -> pd.DataFrame:
    """Return a re-ordered copy of ``df`` using the canonical rank authority.

    - Rows with a valid positive numeric ``Opportunity_Rank`` come first,
      sorted by that rank ascending.
    - Remaining rows follow, sorted by ``Confidence_Adjusted_Score`` desc,
      then ``Symbol`` asc.
    - ``Final_Score`` is never consulted.
    - When ``eligible_only`` is False (default) EVERY row is retained so the
      Candidates Workbench can display ineligible / flagged / Avoid names.
    - The input DataFrame is never mutated.
    """
    if df is None or len(df) == 0:
        return df.copy() if df is not None else pd.DataFrame()

    out = df.copy()

    if eligible_only:
        out = out[is_eligible(out)].copy()
        if out.empty:
            return out

    # Build deterministic sort keys without mutating original columns.
    rank_num = _valid_rank(out[RANK_COL]) if RANK_COL in out.columns else pd.Series(np.nan, index=out.index)
    has_rank = rank_num.notna().astype(int)  # 1 first, 0 second

    if PRIMARY_SCORE_COL in out.columns:
        cas = pd.to_numeric(out[PRIMARY_SCORE_COL], errors="coerce")
    else:
        cas = pd.Series(np.nan, index=out.index)

    sym = out[SYMBOL_COL].astype(str) if SYMBOL_COL in out.columns else pd.Series("", index=out.index)

    keys = pd.DataFrame({
        "_has_rank": has_rank,           # desc: rows with rank first
        "_rank": rank_num,               # asc within ranked
        "_cas": cas,                     # desc for tiebreak / unranked
        "_sym": sym,                     # asc final tiebreak
    })
    order = keys.sort_values(
        by=["_has_rank", "_rank", "_cas", "_sym"],
        ascending=[False, True, False, True],
        kind="mergesort",  # stable
        na_position="last",
    ).index

    return out.loc[order].reset_index(drop=True)


def top_official_candidates(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    """Official Top-N used by Overview, Trade Plan and news coverage.

    Excludes ineligible rows. Deterministic order = canonical_order(eligible_only=True).
    """
    if df is None or len(df) == 0 or n <= 0:
        return pd.DataFrame(columns=df.columns) if df is not None else pd.DataFrame()
    ordered = canonical_order(df, eligible_only=True)
    return ordered.head(int(n)).reset_index(drop=True)


__all__ = [
    "ELIGIBLE_COL", "RANK_COL", "PRIMARY_SCORE_COL", "SECONDARY_SCORE_COL",
    "is_eligible", "canonical_order", "top_official_candidates",
]
