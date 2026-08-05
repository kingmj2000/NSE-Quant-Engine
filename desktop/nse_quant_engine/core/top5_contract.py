"""ONE official Top-5 contract.

Every artifact named ``top5_*`` must describe the exact same symbols in the
exact same order. This module is the only place that order is decided.

Authority (fixed):
1. Exclude rows whose ``Trade_Status`` contains "Avoid"
2. ``Opportunity_Rank`` ascending where valid (positive numeric)
3. otherwise ``Confidence_Adjusted_Score`` descending
4. ``Symbol`` ascending

``Final_Score`` is NEVER consulted.

The correlation-aware diversified basket is a separate, explicitly
non-authoritative proposal (see ``portfolio_diversified_proposal.csv``); it must
never replace the official Top-5.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

OFFICIAL_N = 5
DIVERSIFIED_PROPOSAL_CSV = "portfolio_diversified_proposal.csv"
DIVERSIFIED_CORR_CSV = "portfolio_diversified_corr_matrix.csv"


def _reviewable(plan: pd.DataFrame) -> pd.DataFrame:
    if plan is None or len(plan) == 0:
        return pd.DataFrame()
    df = plan.copy()
    if "Trade_Status" in df.columns:
        df = df[~df["Trade_Status"].astype(str)
                .str.contains("Avoid", case=False, na=False)]
    return df.copy()


def official_order(plan: pd.DataFrame) -> pd.DataFrame:
    """Reviewable rows in official order (all of them, not just five)."""
    df = _reviewable(plan)
    if df.empty:
        return df
    try:
        from .candidate_selection import canonical_order
        return canonical_order(df)
    except Exception:
        cols, asc = [], []
        if "Confidence_Adjusted_Score" in df.columns:
            cols.append("Confidence_Adjusted_Score"); asc.append(False)
        if "Symbol" in df.columns:
            cols.append("Symbol"); asc.append(True)
        if cols:
            df = df.sort_values(cols, ascending=asc, kind="mergesort")
        return df.reset_index(drop=True)


def official_top5(plan: pd.DataFrame, n: int = OFFICIAL_N) -> pd.DataFrame:
    """The official reviewable Top-5 (DataFrame slice, official order)."""
    ordered = official_order(plan)
    if ordered is None or ordered.empty:
        return ordered if ordered is not None else pd.DataFrame()
    return ordered.head(int(n)).reset_index(drop=True)


def official_top5_symbols(plan: pd.DataFrame, n: int = OFFICIAL_N) -> list[str]:
    top = official_top5(plan, n)
    if top is None or top.empty or "Symbol" not in top.columns:
        return []
    return [str(s) for s in top["Symbol"].tolist()]


def read_official_top5(output_dir: Path, n: int = OFFICIAL_N) -> pd.DataFrame:
    """Load trade_plan_latest.csv and return the official Top-5. Never raises."""
    p = Path(output_dir) / "trade_plan_latest.csv"
    if not p.exists():
        return pd.DataFrame()
    try:
        return official_top5(pd.read_csv(p), n)
    except Exception:
        return pd.DataFrame()


__all__ = [
    "OFFICIAL_N", "DIVERSIFIED_PROPOSAL_CSV", "DIVERSIFIED_CORR_CSV",
    "official_order", "official_top5", "official_top5_symbols",
    "read_official_top5",
]
