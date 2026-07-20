"""Integration test — Confidence_Adjusted_Score is the authoritative ranker.

Guardrail: with AAA having the highest Final_Score and BBB the highest CAS,
BBB MUST rank above AAA in every official output (canonical order, official
Top-N, cross-sectional bucketing).
"""
from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.candidate_selection import canonical_order, top_official_candidates  # noqa: E402
from cross_sectional_validation import assign_buckets  # noqa: E402


def _fixture() -> pd.DataFrame:
    # BBB highest CAS, AAA highest Final_Score. Both eligible.
    rows = []
    for sym, cas, fs in [("AAA", 70.0, 99.0), ("BBB", 90.0, 60.0), ("CCC", 55.0, 80.0)]:
        rows.append({
            "Symbol": sym,
            "Opportunity_Eligible": "Yes",
            "Opportunity_Rank": pd.NA,
            "Confidence_Adjusted_Score": cas,
            "Final_Score": fs,
        })
    df = pd.DataFrame(rows)
    # Emulate the official ranking generator: rank by CAS desc, Symbol asc.
    df = df.sort_values(["Confidence_Adjusted_Score", "Symbol"], ascending=[False, True]).reset_index(drop=True)
    df["Opportunity_Rank"] = range(1, len(df) + 1)
    return df


def test_bbb_ranks_above_aaa_in_canonical_order():
    df = _fixture()
    ordered = list(canonical_order(df)["Symbol"])
    assert ordered.index("BBB") < ordered.index("AAA"), \
        f"CAS authority violated: {ordered}"


def test_bbb_top_of_official_topn():
    df = _fixture()
    top = top_official_candidates(df, 3)
    assert list(top["Symbol"])[0] == "BBB"


def test_cross_sectional_bucketing_uses_cas():
    # Build enough rows for the >=10-row per-date threshold.
    base = _fixture()
    extras = pd.DataFrame([
        {"Symbol": f"X{i}", "Opportunity_Eligible": "Yes",
         "Confidence_Adjusted_Score": 40.0 - i, "Final_Score": 95.0 - i,
         "Opportunity_Rank": pd.NA}
        for i in range(10)
    ])
    scored = pd.concat([base, extras], ignore_index=True)
    scored["Signal_Date"] = pd.Timestamp("2026-01-15")
    scored["Horizon_Days"] = 10
    scored["Net_Forward_Return"] = 0.0
    out = assign_buckets(scored)
    ranks = out.set_index("Symbol")["Score_Rank_On_Date"].to_dict()
    assert ranks["BBB"] < ranks["AAA"], \
        f"cross-sectional bucketing ranked AAA above BBB (CAS ignored): {ranks}"
