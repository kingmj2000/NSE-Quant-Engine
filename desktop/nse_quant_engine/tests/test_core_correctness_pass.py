"""Core-correctness pass: Bayesian verdict authority, official Top-5 contract,
symbol-set alignment and downstream validation gating.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import validation_status as vs  # noqa: E402
from core import top5_contract as t5c  # noqa: E402
from core import portfolio_validation as pv  # noqa: E402
from core import expected_value as ev  # noqa: E402
from core import position_sizer as ps  # noqa: E402
from core import rebalance_diff as rd  # noqa: E402

RULES = {
    "CrossVal_Min_Dates": 10,
    "CrossVal_Min_Effective_Dates": 6,
    "CrossVal_Min_Obs": 50,
    "CrossVal_Min_Spread": 0.005,
    "CrossVal_Min_HitRate": 0.55,
    "CrossVal_Min_TStat": 1.50,
    "CrossVal_Min_Bootstrap_Prob": 0.70,
}


# ── 1. Bayesian shrinkage controls the verdict ─────────────────────────────

def test_raw_pass_but_shrunk_fail_is_not_positive():
    """Raw stats sit exactly on every gate; shrinkage pulls them under."""
    raw = {
        "validation_dates": 12,
        "effective_validation_dates": 8,
        "avg_obs": 60,
        "spread": 0.005,
        "hit_rate": 0.55,
        "adj_tstat": 1.50,
        "bootstrap_prob": 0.75,
    }
    assert vs.decide_verdict(raw, RULES)[0] == "Validation Positive"

    verdict, grade, stats = vs.resolve_validation(raw, RULES)
    assert verdict != "Validation Positive", (verdict, stats)
    assert stats["hit_rate"] < raw["hit_rate"]
    assert stats["hit_rate_raw"] == raw["hit_rate"]
    assert stats["adj_tstat"] < raw["adj_tstat"]
    assert grade != "Strong Evidence"


def test_strong_large_sample_survives_shrinkage():
    raw = {
        "validation_dates": 400,
        "effective_validation_dates": 300,
        "avg_obs": 200,
        "spread": 0.05,
        "hit_rate": 0.80,
        "adj_tstat": 6.0,
        "bootstrap_prob": 0.99,
    }
    verdict, grade, stats = vs.resolve_validation(raw, RULES)
    assert verdict == "Validation Positive"
    assert grade == "Strong Evidence"
    assert stats["spread"] < raw["spread"]


def test_write_status_records_bayesian_basis(tmp_path):
    verdict, grade, stats = vs.resolve_validation(
        {"validation_dates": 2, "effective_validation_dates": 1, "avg_obs": 5,
         "spread": 0.0, "hit_rate": 0.5, "adj_tstat": 0.0, "bootstrap_prob": 0.5},
        RULES)
    out = vs.write_status(tmp_path / "validation_status.json", verdict, grade, stats)
    assert out["verdict_basis"] == "bayesian_adjusted"
    assert out["bayesian_shrinkage_applied"] is True
    assert out["verdict"] == verdict


def test_thresholds_come_from_supplied_rules():
    stats = {"validation_dates": 12, "effective_validation_dates": 8, "avg_obs": 60,
             "spread": 0.01, "hit_rate": 0.60, "adj_tstat": 2.0, "bootstrap_prob": 0.75}
    assert vs.decide_verdict(stats, RULES)[0] == "Validation Positive"
    strict = dict(RULES, CrossVal_Min_Bootstrap_Prob=0.99)
    assert vs.decide_verdict(stats, strict)[0] == "No Proven Edge Yet"


def test_cross_sectional_module_uses_one_path():
    import cross_sectional_validation as csv_mod
    summary = pd.DataFrame([{
        "Horizon_Days": 10,
        "Validation_Dates": 12,
        "Effective_Validation_Dates": 8,
        "Avg_Obs_All": 60,
        "Avg_TopMinusBottom_Quintile": 0.005,
        "Hit_Rate_TopBeatsBottom": 0.55,
        "Adjusted_TStat_TopMinusBottom": 1.5,
        "Bootstrap_Prob_Positive": 0.75,
    }])
    rules = dict(RULES, CrossVal_Horizon=10)
    verdict, grade, stats = csv_mod.resolve_validation(summary, rules)
    assert verdict != "Validation Positive"
    assert csv_mod.validation_verdict(summary, rules) == verdict
    assert csv_mod.evidence_grade(summary, rules) == grade
    assert "hit_rate_raw" in stats


# ── 2/3. Official Top-5 contract & symbol-set alignment ────────────────────

def _plan() -> pd.DataFrame:
    return pd.DataFrame([
        {"Symbol": "E", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 60, "Final_Score": 99},
        {"Symbol": "A", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 95, "Final_Score": 10},
        {"Symbol": "F", "Trade_Status": "Avoid", "Confidence_Adjusted_Score": 99, "Final_Score": 99},
        {"Symbol": "C", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 80, "Final_Score": 50},
        {"Symbol": "B", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 90, "Final_Score": 20},
        {"Symbol": "D", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 70, "Final_Score": 95},
        {"Symbol": "G", "Trade_Status": "Watch", "Confidence_Adjusted_Score": 50, "Final_Score": 5},
    ])


def test_official_top5_is_cas_ordered_and_excludes_avoid():
    assert t5c.official_top5_symbols(_plan()) == ["A", "B", "C", "D", "E"]


def test_official_top5_prefers_opportunity_rank():
    plan = _plan()
    plan["Opportunity_Rank"] = [1, 2, 0, 3, 4, 5, 6]
    assert t5c.official_top5_symbols(plan) == ["E", "A", "C", "B", "D"]


def test_diversified_set_never_contaminates_official(tmp_path):
    """Official A,B,C,D,E vs diversified A,F,B,C,D — official wins everywhere."""
    out = tmp_path / "output"
    out.mkdir()
    _plan().to_csv(out / "trade_plan_latest.csv", index=False)
    official = t5c.official_top5_symbols(pd.read_csv(out / "trade_plan_latest.csv"))
    assert official == ["A", "B", "C", "D", "E"]

    diversified = ["A", "F", "B", "C", "D"]
    pd.DataFrame({"Symbol": diversified,
                  "Authority": "NON_AUTHORITATIVE_DIVERSIFIED_PROPOSAL"}).to_csv(
        out / t5c.DIVERSIFIED_PROPOSAL_CSV, index=False)

    for name in ["top5_position_sizing.csv", "top5_sector_context.csv",
                 "top5_events.csv", "top5_expected_value.csv"]:
        pd.DataFrame({"Symbol": official}).to_csv(out / name, index=False)
    pd.DataFrame(index=official, columns=official).to_csv(out / "top5_corr_matrix.csv")

    rep = pv.validate_batch(out)
    assert rep["expected_symbols"] == official
    assert rep["symbol_set_aligned"] is True
    assert sorted(rep["sizing_symbols"]) == official


def test_symbol_set_mismatch_downgrades(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    _plan().to_csv(out / "trade_plan_latest.csv", index=False)
    (out / "validation_status.json").write_text(json.dumps(
        {"verdict": "Validation Positive", "evidence_grade": "Strong Evidence"}))
    pd.DataFrame({"Symbol": ["A", "F", "B", "C", "D"]}).to_csv(
        out / "top5_position_sizing.csv", index=False)

    rep = pv.validate_batch(out)
    assert rep["symbol_set_aligned"] is False
    assert rep["verdict"] == "Downgrade_To_Watch"
    assert any("symbol-set mismatch" in r for r in rep["reasons"])


# ── 4. Downstream gating when validation is not positive ───────────────────

@pytest.mark.parametrize("verdict", ["Insufficient History", "Validation Negative",
                                     "No Proven Edge Yet"])
def test_portfolio_validation_never_ships_without_positive_validation(tmp_path, verdict):
    out = tmp_path / "output"
    out.mkdir()
    (out / "validation_status.json").write_text(json.dumps({"verdict": verdict}))
    rep = pv.validate_batch(out)
    assert rep["verdict"] == "Downgrade_To_Watch"
    assert any("official validation not positive" in r for r in rep["reasons"])


@pytest.mark.parametrize("verdict", ["Insufficient History", "No Proven Edge Yet"])
def test_ev_report_is_diagnostic_only(verdict):
    top5 = pd.DataFrame({"Symbol": ["A", "B"]})
    bt = pd.DataFrame([{"Variant": "Top5", "Hit_Rate": 0.7,
                        "AvgWin_%": 5.0, "AvgLoss_%": -2.0}])
    sizing = pd.DataFrame({"Symbol": ["A", "B"], "Weight_%": [20.0, 20.0]})
    rep = ev.top5_ev_report(top5, bt, None, sizing, validation_verdict=verdict)
    assert set(rep["Basis"]) == {"STYLE_BACKTEST_DIAGNOSTIC"}
    assert set(rep["Decision_Use"]) == {"Not_For_Decisions_Watchlist_Only"}
    assert rep["Kelly_Fraction_Capped"].isna().all()
    assert set(rep["Validation_Verdict"]) == {verdict}
    assert not any("Yes" == str(a) for a in rep["EV_Sizing_Agree"])


def test_ev_report_actionable_when_validated():
    top5 = pd.DataFrame({"Symbol": ["A"]})
    bt = pd.DataFrame([{"Variant": "Top5", "Hit_Rate": 0.7,
                        "AvgWin_%": 5.0, "AvgLoss_%": -2.0}])
    sizing = pd.DataFrame({"Symbol": ["A"], "Weight_%": [20.0]})
    rep = ev.top5_ev_report(top5, bt, None, sizing,
                            validation_verdict="Validation Positive")
    assert rep.loc[0, "Basis"] == "VALIDATED_EV"
    assert pd.notna(rep.loc[0, "Kelly_Fraction_Capped"])


def test_position_sizing_labelled_hypothetical_by_default():
    top5 = pd.DataFrame({"Symbol": ["A", "B"], "Price": [100.0, 200.0],
                         "Stop_Loss": [95.0, 190.0]})
    sized = ps.size_portfolio(top5)
    assert set(sized["Sizing_Basis"]) == {"HYPOTHETICAL_WATCHLIST_SIZING"}
    sized_ok = ps.size_portfolio(top5, validation_positive=True)
    assert set(sized_ok["Sizing_Basis"]) == {"VALIDATED_SIZING"}


def test_rebalance_is_watchlist_only_without_validation(tmp_path):
    prev = tmp_path / "top5_prev.csv"
    pd.DataFrame({"Symbol": ["A", "B", "C", "D", "E"],
                  "Confidence_Adjusted_Score": [90, 80, 70, 60, 50]}).to_csv(prev, index=False)
    curr = pd.DataFrame({"Symbol": ["A", "B", "C", "D", "Z"],
                         "Confidence_Adjusted_Score": [90, 80, 70, 60, 95]})
    rep = rd.build(curr, prev, all_curr=curr, validation_positive=False)
    assert rep["recommendation"] == "Watchlist_Changes_Only"
    assert rep["decision_use"] == "Watchlist_Context_Only"
    assert rep["entries"] == ["Z"] and rep["exits"] == ["E"]
    assert rep["estimated_turnover_%"] == 20.0


def test_rebalance_first_run_is_watchlist_only(tmp_path):
    curr = pd.DataFrame({"Symbol": ["A"], "Confidence_Adjusted_Score": [90]})
    rep = rd.build(curr, tmp_path / "nope.csv", validation_positive=False)
    assert rep["recommendation"] == "Watchlist_Changes_Only"


def test_exit_reason_ignores_final_score():
    prev_row = pd.Series({"Symbol": "A", "Final_Score": 99.0})
    all_curr = pd.DataFrame([{"Symbol": "A", "Final_Score": 1.0}])
    reason = rd._exit_reason("A", prev_row, all_curr, None, None)
    assert "score decay" not in reason
