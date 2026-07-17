"""Tests for core.candidate_selection — canonical ordering + official Top-N.

Guardrails covered:
- Ordering never mutates input.
- Ineligible rows retained when eligible_only=False.
- Official Top-N excludes ineligible rows.
- Final_Score never affects order.
- Generic Risk_Flag values are NOT treated as hard vetoes.
- Invalid / duplicate Opportunity_Rank handled deterministically.
- Shadow columns cannot alter official ordering.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.candidate_selection import (  # noqa: E402
    canonical_order, top_official_candidates, is_eligible,
)


def _df():
    return pd.DataFrame([
        # Symbol, Opportunity_Eligible, Opportunity_Rank, CAS, Final_Score, Risk_Flag
        {"Symbol": "AAA", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 2,  "Confidence_Adjusted_Score": 80, "Final_Score": 60, "Risk_Flag": ""},
        {"Symbol": "BBB", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 1,  "Confidence_Adjusted_Score": 70, "Final_Score": 99, "Risk_Flag": "elevated_vol"},
        {"Symbol": "CCC", "Opportunity_Eligible": "No",  "Opportunity_Rank": np.nan, "Confidence_Adjusted_Score": 95, "Final_Score": 95, "Risk_Flag": ""},
        {"Symbol": "DDD", "Opportunity_Eligible": "Yes", "Opportunity_Rank": np.nan, "Confidence_Adjusted_Score": 65, "Final_Score": 90, "Risk_Flag": ""},
        {"Symbol": "EEE", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 3,  "Confidence_Adjusted_Score": 60, "Final_Score": 55, "Risk_Flag": "overbought"},
    ])


def test_input_not_mutated():
    df = _df()
    snap = df.copy(deep=True)
    _ = canonical_order(df)
    _ = top_official_candidates(df, 3)
    pd.testing.assert_frame_equal(df, snap)


def test_eligible_only_false_retains_all_rows():
    df = _df()
    out = canonical_order(df, eligible_only=False)
    assert len(out) == len(df)
    assert set(out["Symbol"]) == set(df["Symbol"])


def test_official_topn_excludes_ineligible():
    df = _df()
    top = top_official_candidates(df, 10)
    assert "CCC" not in set(top["Symbol"]), "ineligible row leaked into official Top-N"


def test_rank_authority_wins_and_ranked_come_first():
    df = _df()
    out = canonical_order(df)
    # BBB rank1, AAA rank2, EEE rank3 → ranked block first
    symbols = list(out["Symbol"])
    assert symbols[:3] == ["BBB", "AAA", "EEE"]
    # Unranked eligible/ineligible follow, sorted by CAS desc
    assert set(symbols[3:]) == {"CCC", "DDD"}
    assert symbols[3] == "CCC"  # CAS 95 > 65
    assert symbols[4] == "DDD"


def test_final_score_ignored():
    df = _df()
    # Flip Final_Score so it would drastically reorder if used
    df2 = df.copy(); df2["Final_Score"] = [1, 2, 3, 4, 5]
    a = list(canonical_order(df)["Symbol"])
    b = list(canonical_order(df2)["Symbol"])
    assert a == b


def test_generic_risk_flag_is_not_hard_veto():
    df = _df()
    # BBB has Risk_Flag='elevated_vol' but is Eligible=Yes and Rank=1
    top = top_official_candidates(df, 5)
    assert list(top["Symbol"])[0] == "BBB"


def test_duplicate_rank_deterministic_tiebreak_by_cas_then_symbol():
    df = pd.DataFrame([
        {"Symbol": "ZZZ", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 1, "Confidence_Adjusted_Score": 70, "Final_Score": 10},
        {"Symbol": "AAA", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 1, "Confidence_Adjusted_Score": 80, "Final_Score": 99},
        {"Symbol": "MMM", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 1, "Confidence_Adjusted_Score": 80, "Final_Score": 50},
    ])
    out = canonical_order(df)
    # Same rank → CAS desc, then Symbol asc: AAA(80) < MMM(80) < ZZZ(70)
    assert list(out["Symbol"]) == ["AAA", "MMM", "ZZZ"]


def test_invalid_rank_values_pushed_after_valid():
    df = pd.DataFrame([
        {"Symbol": "X", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 0,   "Confidence_Adjusted_Score": 99, "Final_Score": 1},
        {"Symbol": "Y", "Opportunity_Eligible": "Yes", "Opportunity_Rank": -3,  "Confidence_Adjusted_Score": 98, "Final_Score": 1},
        {"Symbol": "Z", "Opportunity_Eligible": "Yes", "Opportunity_Rank": 5,   "Confidence_Adjusted_Score": 10, "Final_Score": 1},
        {"Symbol": "W", "Opportunity_Eligible": "Yes", "Opportunity_Rank": "bad","Confidence_Adjusted_Score": 50, "Final_Score": 1},
    ])
    out = canonical_order(df)
    assert list(out["Symbol"])[0] == "Z"   # only valid rank
    # The rest sorted by CAS desc: X(99), Y(98), W(50)
    assert list(out["Symbol"])[1:] == ["X", "Y", "W"]


def test_shadow_columns_do_not_affect_official_ordering():
    df = _df()
    df_shadow = df.copy()
    df_shadow["Shadow_Rank"] = [5, 4, 3, 2, 1]
    df_shadow["Score_Shadow"] = [1, 2, 3, 4, 5]
    df_shadow["Final_Score_Shadow"] = [99, 99, 99, 99, 99]
    a = list(canonical_order(df)["Symbol"])
    b = list(canonical_order(df_shadow)["Symbol"])
    assert a == b


def test_empty_input():
    empty = pd.DataFrame(columns=["Symbol", "Opportunity_Eligible", "Opportunity_Rank", "Confidence_Adjusted_Score"])
    assert canonical_order(empty).empty
    assert top_official_candidates(empty, 5).empty


def test_missing_columns_do_not_crash():
    df = pd.DataFrame([{"Symbol": "A"}, {"Symbol": "B"}])
    out = canonical_order(df)
    assert set(out["Symbol"]) == {"A", "B"}
    # No Opportunity_Eligible column → nothing is officially eligible
    assert top_official_candidates(df, 5).empty


def test_is_eligible_case_insensitive():
    df = pd.DataFrame([
        {"Symbol": "A", "Opportunity_Eligible": "yes"},
        {"Symbol": "B", "Opportunity_Eligible": "YES"},
        {"Symbol": "C", "Opportunity_Eligible": "No"},
        {"Symbol": "D", "Opportunity_Eligible": " Yes "},
    ])
    m = is_eligible(df)
    assert list(m) == [True, True, False, True]
