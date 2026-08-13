"""Fail-closed integrity regressions.

Every test here covers a defect that survived a fully green suite. The common
shape of all four: a check that could not be performed was treated as a check
that passed. The pre-existing tests only ever exercised the paths where the
evidence was PRESENT (present-but-mismatched artifacts, validation-not-positive,
filters whose column existed), so the absent-evidence branch was never asserted.

Each block names the original defect so a future refactor cannot quietly
reintroduce it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core import expected_value as evmod
from core import portfolio_validation as pv

OFFICIAL = ["AAA", "BBB", "CCC", "DDD", "EEE"]
CRITICAL = ["top5_corr_matrix.csv", "top5_position_sizing.csv",
            "top5_sector_context.csv", "top5_events.csv",
            "top5_expected_value.csv"]


def _plan(out: Path, symbols=None) -> None:
    symbols = symbols or OFFICIAL
    n = len(symbols)
    pd.DataFrame({
        "Symbol": symbols,
        "Confidence_Adjusted_Score": [90.0 - i for i in range(n)],
        "Trade_Status": ["Consider"] * n,
    }).to_csv(out / "trade_plan_latest.csv", index=False)


def _validated(out: Path) -> None:
    (out / "validation_status.json").write_text(json.dumps(
        {"verdict": "Validation Positive", "evidence_grade": "Strong Evidence",
         "stats": {}}), encoding="utf-8")
    (out / "alpha_zoo_survivors.json").write_text(
        json.dumps(["a1", "a2", "a3"]), encoding="utf-8")
    (out / "macro_context.json").write_text('{"regime":"NEUTRAL"}', encoding="utf-8")


def _write_artifacts(out: Path, symbols=None) -> None:
    symbols = symbols or OFFICIAL
    pd.DataFrame(index=symbols, columns=symbols).to_csv(out / "top5_corr_matrix.csv")
    pd.DataFrame({"Symbol": symbols,
                  "Weight_%": [20.0] * len(symbols),
                  "Max_Loss_%_of_NAV": [0.4] * len(symbols)}) \
        .to_csv(out / "top5_position_sizing.csv", index=False)
    pd.DataFrame({"Symbol": symbols,
                  "Sector": [f"S{i}" for i in range(len(symbols))]}) \
        .to_csv(out / "top5_sector_context.csv", index=False)
    pd.DataFrame({"Symbol": symbols,
                  "Event_Risk_Flag": ["None"] * len(symbols)}) \
        .to_csv(out / "top5_events.csv", index=False)
    pd.DataFrame({"Symbol": symbols, "EV_%": [1.0] * len(symbols)}) \
        .to_csv(out / "top5_expected_value.csv", index=False)


# ── Issue A — portfolio validator must not fail open ────────────────────────
# Defect: `if syms and syms != expected` skipped empty symbol lists, so a
# MISSING artifact counted as "no mismatch". With validation positive and a
# valid Top-5, validate_batch returned Ship with zero artifacts on disk.

def test_missing_artifacts_downgrade_even_when_validation_positive(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    _plan(out)
    _validated(out)
    # Every critical top5_* artifact deliberately absent.

    rep = pv.validate_batch(out)

    assert rep["verdict"] == "Downgrade_To_Watch", rep
    assert rep["artifact_completeness"] is False
    assert sorted(rep["missing_artifacts"]) == sorted(CRITICAL), rep["missing_artifacts"]
    assert any("missing/empty critical portfolio artifact" in r for r in rep["reasons"])


@pytest.mark.parametrize("dropped", CRITICAL)
def test_any_single_missing_artifact_downgrades(tmp_path, dropped):
    out = tmp_path / "output"
    out.mkdir()
    _plan(out)
    _validated(out)
    _write_artifacts(out)
    (out / dropped).unlink()

    rep = pv.validate_batch(out)

    assert rep["verdict"] == "Downgrade_To_Watch", (dropped, rep)
    assert rep["artifact_completeness"] is False
    assert rep["missing_artifacts"] == [dropped]


def test_artifact_present_but_symbol_less_counts_as_missing(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    _plan(out)
    _validated(out)
    _write_artifacts(out)
    # File exists but carries no Symbol column — unverifiable, not passing.
    pd.DataFrame({"Weight_%": [20.0] * 5}).to_csv(
        out / "top5_position_sizing.csv", index=False)

    rep = pv.validate_batch(out)

    assert rep["verdict"] == "Downgrade_To_Watch"
    assert "top5_position_sizing.csv" in rep["missing_artifacts"]


def test_missing_official_top5_downgrades(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    _validated(out)
    _write_artifacts(out)
    # trade_plan_latest.csv absent -> nothing to validate against.

    rep = pv.validate_batch(out)

    assert rep["verdict"] == "Downgrade_To_Watch"
    assert rep["expected_symbols"] == []
    assert rep["artifact_completeness"] is False
    assert rep["symbol_set_aligned"] is False
    assert rep["symbol_order_aligned"] is False
    assert any("official Top-5 unavailable" in r for r in rep["reasons"])


def test_symbol_order_mismatch_downgrades(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    _plan(out)
    _validated(out)
    _write_artifacts(out)
    # Same symbols, reversed order: a positional join would pair the wrong
    # numbers to the wrong symbols.
    pd.DataFrame({"Symbol": list(reversed(OFFICIAL)),
                  "Weight_%": [20.0] * 5,
                  "Max_Loss_%_of_NAV": [0.4] * 5}) \
        .to_csv(out / "top5_position_sizing.csv", index=False)

    rep = pv.validate_batch(out)

    assert rep["symbol_set_aligned"] is True     # set is fine
    assert rep["symbol_order_aligned"] is False  # order is not
    assert rep["verdict"] == "Downgrade_To_Watch"
    assert any("symbol-order mismatch" in r for r in rep["reasons"])


def test_complete_and_aligned_evidence_can_ship(tmp_path):
    """The gate must still open when the evidence really is complete."""
    out = tmp_path / "output"
    out.mkdir()
    _plan(out)
    _validated(out)
    _write_artifacts(out)

    rep = pv.validate_batch(out)

    assert rep["artifact_completeness"] is True
    assert rep["missing_artifacts"] == []
    assert rep["symbol_set_aligned"] is True
    assert rep["symbol_order_aligned"] is True
    assert rep["expected_symbols"] == OFFICIAL
    assert rep["verdict"] in ("Ship", "Ship_With_Caveats"), rep


def test_report_json_carries_all_completeness_fields(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    _plan(out)
    _validated(out)
    rep = pv.validate_batch(out)
    pv.write_report(out, rep)
    data = json.loads((out / "portfolio_validation.json").read_text(encoding="utf-8"))
    for field in ("expected_symbols", "corr_symbols", "sizing_symbols",
                  "sector_symbols", "event_symbols", "ev_symbols",
                  "missing_artifacts", "artifact_completeness",
                  "symbol_set_aligned", "symbol_order_aligned"):
        assert field in data, field


# ── Issue B — shadow must not be compared against a copy of official ───────
# Defect: the shadow frame is built as `out = old.copy()` and therefore inherits
# the official Confidence_Adjusted_Score. The report's fallback chain preferred
# that column, comparing official-against-itself and reporting Spearman 1.00 /
# Jaccard 1.00 as perfect agreement.

def _shadow_module():
    return pytest.importorskip("nse_quant_engine_v4_shadow")


def test_shadow_output_has_no_bare_official_score_columns():
    mod = _shadow_module()
    frame = pd.DataFrame({
        "Symbol": ["AAA", "BBB"],
        "Confidence_Adjusted_Score": [90.0, 80.0],
        "Final_Score": [95.0, 70.0],
        "Opportunity_Score": [60.0, 50.0],
        "V4_Confidence_Adjusted_Score": [10.0, 99.0],
    })
    clean = mod.sanitize_shadow_columns(frame)

    for leaked in ("Confidence_Adjusted_Score", "Final_Score", "Opportunity_Score"):
        assert leaked not in clean.columns, leaked
    assert "V4_Confidence_Adjusted_Score" in clean.columns
    assert "Official_Final_Score_Diagnostic" in clean.columns
    # The original frame must not be mutated.
    assert "Final_Score" in frame.columns


def test_shadow_sanitizer_drops_duplicate_when_official_already_captured():
    mod = _shadow_module()
    frame = pd.DataFrame({
        "Symbol": ["AAA"],
        "Confidence_Adjusted_Score": [90.0],
        "Official_Confidence_Adjusted_Score": [90.0],
        "V4_Confidence_Adjusted_Score": [11.0],
    })
    clean = mod.sanitize_shadow_columns(frame)
    assert "Confidence_Adjusted_Score" not in clean.columns
    assert clean["Official_Confidence_Adjusted_Score"].iloc[0] == 90.0


def test_shadow_report_refuses_self_comparison(tmp_path, monkeypatch):
    """A shadow file carrying only the copied official CAS must not compare."""
    import shadow_vs_official_report as rep

    out = tmp_path / "output"
    out.mkdir()
    official = pd.DataFrame({"Symbol": ["AAA", "BBB", "CCC"],
                             "Confidence_Adjusted_Score": [9.0, 8.0, 7.0]})
    official.to_csv(out / "latest_scores.csv", index=False)
    # Exactly the old failure mode: official CAS copied in, no V4 column.
    official.copy().to_csv(out / "latest_scores_v4_shadow.csv", index=False)

    monkeypatch.setattr(rep, "OUT", out)
    monkeypatch.setattr(rep, "OFFICIAL", out / "latest_scores.csv")
    monkeypatch.setattr(rep, "SHADOW", out / "latest_scores_v4_shadow.csv")
    monkeypatch.setattr(rep, "STATUS_OFF", out / "validation_status.json")
    monkeypatch.setattr(rep, "STATUS_SHA", out / "validation_status_shadow.json")
    monkeypatch.setattr(rep, "FWD", out / "forward_return_history.csv")
    monkeypatch.setattr(rep, "REPORT", out / "shadow_vs_official.md")
    monkeypatch.setattr(rep, "CSV", out / "shadow_vs_official.csv")

    summary = rep.build()

    assert summary["recommendation"] == "INSUFFICIENT_DATA"
    assert "V4_Confidence_Adjusted_Score" in summary["reason"] \
        or "official_column" in summary["reason"]
    assert "spearman_full" not in summary


def test_shadow_report_never_recommends_promotion():
    """No output path may describe the shadow engine as a champion."""
    import inspect

    import shadow_vs_official_report as rep

    src = inspect.getsource(rep)
    for banned in ("consider manual switch", "keep current champion",
                   "shadow leads", "switch to shadow"):
        assert banned not in src.lower(), banned
    assert "CURRENT-RANKING DIAGNOSTIC ONLY" in src


# ── Issue D — EV must fail closed on a filter it cannot apply ──────────────
# Defect: _apply_filters silently dropped filter columns that did not exist, and
# the caller filtered on "Score_Bucket" / "Top Quintile" — neither of which this
# engine ever writes (the column is Signal_Bucket). The filter was therefore
# ignored 100% of the time and an UNFILTERED EV was published under a filtered
# label.

def _fwd(bucket_col: str | None = "Signal_Bucket", n: int = 100) -> pd.DataFrame:
    data = {"Horizon_Days": [10] * n,
            "Net_Forward_Return": [0.03] * 60 + [-0.02] * 40}
    if bucket_col:
        data[bucket_col] = ["Top Candidate"] * n
    return pd.DataFrame(data)


VALIDATED = {"verdict": "Validation Positive"}


def test_ev_refuses_when_filter_column_absent():
    res = evmod.expected_value_per_day(
        _fwd(), VALIDATED, horizon=10, filters={"Score_Bucket": "Top Quintile"})

    assert np.isnan(res["ev_per_day"])
    assert np.isnan(res["ev_per_trade"])
    assert res["missing_filter_columns"] == ["Score_Bucket"]
    assert "unavailable" in res["status"].lower()


def test_ev_computes_with_the_real_bucket_column():
    res = evmod.expected_value_per_day(
        _fwd(), VALIDATED, horizon=10, hold_days=10,
        filters={"Signal_Bucket": "Top Candidate"})

    assert res["missing_filter_columns"] == []
    assert res["n_obs"] == 100
    assert res["ev_per_trade"] > 0


def test_ev_does_not_raise_on_incompatible_history_schema():
    """`Date,Symbol,Fwd_Return` history used to raise KeyError('Horizon_Days')."""
    legacy = pd.DataFrame({"Date": ["2026-01-01"] * 3,
                           "Symbol": ["AAA", "BBB", "CCC"],
                           "Fwd_Return": [0.01, -0.02, 0.03]})

    res = evmod.expected_value_per_day(legacy, VALIDATED, horizon=10)

    assert np.isnan(res["ev_per_day"])
    assert "schema" in res["status"].lower()


def test_ev_filter_label_flags_missing_columns_loudly():
    _, label, missing = evmod._apply_filters(
        _fwd(), {"Signal_Bucket": "Top Candidate", "Nope": "x"})
    assert missing == ["Nope"]
    assert "MISSING_FILTER_COLS" in label


def test_shadow_report_uses_signal_bucket_not_score_bucket():
    import inspect

    import shadow_vs_official_report as rep

    src = inspect.getsource(rep.build)
    assert "Signal_Bucket" in src
    assert "Top Quintile" not in src
