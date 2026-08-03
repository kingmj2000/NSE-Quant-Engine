"""Integration test — Confidence_Adjusted_Score is the authoritative ranker.

Guardrail: AAA has the highest Final_Score, BBB the highest
Confidence_Adjusted_Score. BBB MUST rank above AAA everywhere official, using
the real production functions (no pre-sorted fixtures), and changing only
Final_Score must never change any official order.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nse_quant_engine import assign_official_ranks  # noqa: E402
from core.candidate_selection import canonical_order, top_official_candidates  # noqa: E402
from core.portfolio_selection import diversified_top_n  # noqa: E402
from cross_sectional_validation import assign_buckets  # noqa: E402
from dashboard_html_builder import _official_order  # noqa: E402
from news_market_builder import select_candidates  # noqa: E402
import trade_plan_builder as tpb  # noqa: E402


# AAA: highest Final_Score. BBB: highest Confidence_Adjusted_Score.
ROWS = [
    # deliberately unsorted input order
    {"Symbol": "CCC", "cas": 55.0, "fs": 80.0},
    {"Symbol": "AAA", "cas": 70.0, "fs": 99.0},
    {"Symbol": "BBB", "cas": 90.0, "fs": 60.0},
]


def _raw(final_scores: dict[str, float] | None = None) -> pd.DataFrame:
    """Unranked production-shaped snapshot (no Rank / Opportunity_Rank yet)."""
    rows = []
    for r in ROWS:
        fs = (final_scores or {}).get(r["Symbol"], r["fs"])
        rows.append({
            "Symbol": r["Symbol"],
            "Name": f"{r['Symbol']} Ltd",
            "Universe": "Stock",
            "Universe_Group": "Large Cap",
            "Opportunity_Eligible": "Yes",
            "Confidence_Adjusted_Score": r["cas"],
            "Final_Score": fs,
            "Price": 100.0,
            "ATR_14": 2.0,
            "RSI_14": 55.0,
            "Momentum_Score": 60.0,
            "Risk_Score": 70.0,
            "Liquidity_Score": 70.0,
            "Avg_Traded_Value_20D": 5e7,
            "Risk_Flag": "Clean",
        })
    return pd.DataFrame(rows)


def _official(df: pd.DataFrame) -> pd.DataFrame:
    return assign_official_ranks(df)


def _pos(seq, sym) -> int:
    return list(seq).index(sym)


# ── 1. Official rank generation + Opportunity_Rank ──────────────────────────

def test_official_rank_generation_and_opportunity_rank():
    out = _official(_raw())
    order = list(out["Symbol"])
    assert _pos(order, "BBB") < _pos(order, "AAA"), order
    ranks = out.set_index("Symbol")["Rank"].to_dict()
    opp = out.set_index("Symbol")["Opportunity_Rank"].to_dict()
    assert ranks["BBB"] < ranks["AAA"]
    assert opp["BBB"] < opp["AAA"]


# ── 2. Canonical candidate selection ────────────────────────────────────────

def test_canonical_order_and_top_official():
    out = _official(_raw())
    ordered = list(canonical_order(out)["Symbol"])
    assert _pos(ordered, "BBB") < _pos(ordered, "AAA"), ordered
    assert list(top_official_candidates(out, 3)["Symbol"])[0] == "BBB"


# ── 3. Trade plan ordering ──────────────────────────────────────────────────

def test_trade_plan_orders_bbb_above_aaa():
    out = _official(_raw())
    rules = tpb.load_rules()
    plan = tpb.make_trade_plan(out, rules, "No Proven Edge Yet",
                               "Insufficient Evidence", "validation_status.json")
    assert not plan.empty
    syms = list(plan["Symbol"])
    assert _pos(syms, "BBB") < _pos(syms, "AAA"), syms


# ── 4. Portfolio-selection input ────────────────────────────────────────────

def test_portfolio_selection_input_prefers_cas():
    out = _official(_raw())
    picks = diversified_top_n(out, corr=pd.DataFrame(), n=3)
    assert picks[0] == "BBB", picks
    assert _pos(picks, "BBB") < _pos(picks, "AAA")


def test_portfolio_selection_tie_break_symbol_ascending():
    tied = pd.DataFrame([
        {"Symbol": "ZZZ", "Confidence_Adjusted_Score": 50.0, "Final_Score": 99.0},
        {"Symbol": "AAA", "Confidence_Adjusted_Score": 50.0, "Final_Score": 10.0},
    ])
    assert diversified_top_n(tied, corr=pd.DataFrame(), n=2) == ["AAA", "ZZZ"]
    # reversed input row order must not change the outcome
    assert diversified_top_n(tied.iloc[::-1].reset_index(drop=True),
                             corr=pd.DataFrame(), n=2) == ["AAA", "ZZZ"]


def test_portfolio_selection_without_authoritative_score_is_empty():
    no_cas = pd.DataFrame([{"Symbol": "AAA", "Final_Score": 99.0}])
    assert diversified_top_n(no_cas, corr=pd.DataFrame(), n=2) == []
    invalid = pd.DataFrame([{"Symbol": "AAA", "Confidence_Adjusted_Score": None,
                             "Final_Score": 99.0}])
    assert diversified_top_n(invalid, corr=pd.DataFrame(), n=2) == []


# ── 5. Cross-sectional validation bucket ────────────────────────────────────

def test_cross_sectional_bucketing_uses_cas():
    base = _official(_raw())
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
    assert ranks["BBB"] < ranks["AAA"], ranks


# ── 6. Overview / dashboard official order ──────────────────────────────────

def test_dashboard_official_order():
    out = _official(_raw())
    syms = list(_official_order(out)["Symbol"])
    assert _pos(syms, "BBB") < _pos(syms, "AAA"), syms


# ── 7. News candidate priority ──────────────────────────────────────────────

def test_news_candidate_priority():
    out = _official(_raw())
    cands = select_candidates(out)
    syms = list(cands["Symbol"])
    assert _pos(syms, "BBB") < _pos(syms, "AAA"), syms


# ── 8. Final_Score changes must not move any official order ─────────────────

def test_changing_final_score_does_not_change_official_orders():
    base = _official(_raw())
    flipped = _official(_raw({"AAA": 1.0, "BBB": 100.0, "CCC": 50.0}))

    assert list(base["Symbol"]) == list(flipped["Symbol"])
    assert base.set_index("Symbol")["Rank"].to_dict() == \
        flipped.set_index("Symbol")["Rank"].to_dict()
    assert base.set_index("Symbol")["Opportunity_Rank"].to_dict() == \
        flipped.set_index("Symbol")["Opportunity_Rank"].to_dict()
    assert list(canonical_order(base)["Symbol"]) == list(canonical_order(flipped)["Symbol"])
    assert list(_official_order(base)["Symbol"]) == list(_official_order(flipped)["Symbol"])
    assert diversified_top_n(base, corr=pd.DataFrame(), n=3) == \
        diversified_top_n(flipped, corr=pd.DataFrame(), n=3)
    assert list(select_candidates(base)["Symbol"]) == list(select_candidates(flipped)["Symbol"])
