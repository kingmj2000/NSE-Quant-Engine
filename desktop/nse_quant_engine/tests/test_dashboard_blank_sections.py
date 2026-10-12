"""Regressions for the dashboard sections that were permanently blank or stuck.

Every case is the same family as the rest of this repo's defects: a check that
could not be performed was rendered as if it had been.

  1. delivery_momentum / iv_rank had n_dates=0 forever — price symbols carry
     ".NS", overlay caches store bare symbols, so the lookup never matched.
  2. delivery_momentum, once matching, read the LATEST delivery% for every
     historical eval date (look-ahead into the IC).
  3. "NEW CANDIDATE" was a hard-coded name list in the dashboard JS.
  4. alpha_promotion_log.json was never written, and the reader looked for keys
     the writer does not emit.
  5. "Awaiting maturation" read NaNs from a file that only holds matured rows.
  6. India VIX / Nifty 50D were read from the candidate-universe price file,
     which never contains index tickers; "neutral" was a silent default.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core import alpha_zoo as az
from core import alpha_evaluator as ae
from core import sentiment_overlay as sent

ROOT = Path(__file__).resolve().parents[1]


# ── 1 + 2: overlay alphas ───────────────────────────────────────────────────
@pytest.fixture
def delivery_overlay(monkeypatch):
    dates = pd.bdate_range("2026-07-01", periods=40)
    # delivery% ramps 10 -> 49 so 5d-mean minus 20d-mean is a known positive.
    wide = pd.DataFrame({"KOTAKBANK": np.arange(10.0, 50.0)}, index=dates)
    monkeypatch.setitem(az._OVERLAY_CACHE, "delivery_pct_daily", wide)
    return dates


def _px(sym, dates):
    return pd.DataFrame({"Date": dates, "Symbol": sym, "Close": 100.0})


def test_overlay_alpha_matches_yfinance_symbols(delivery_overlay):
    v = az.alpha_delivery_momentum(_px("KOTAKBANK.NS", delivery_overlay))
    assert pd.notna(v), "'.NS' ticker must resolve to the bare overlay symbol"
    assert v == pytest.approx(7.5)        # mean(45..49) - mean(30..49)


def test_overlay_alpha_has_no_look_ahead(delivery_overlay):
    """Evaluated as of day 25, the alpha may only see days 1-25."""
    asof = delivery_overlay[24]
    v_hist = az.alpha_delivery_momentum(_px("KOTAKBANK.NS", delivery_overlay[:25]))
    v_full = az.alpha_delivery_momentum(_px("KOTAKBANK.NS", delivery_overlay))
    assert pd.notna(v_hist)
    assert v_hist == pytest.approx(7.5)   # same ramp shape, but from days 6..25
    # and it is computed from data <= asof, never the latest tail
    wide = az._OVERLAY_CACHE["delivery_pct_daily"]["KOTAKBANK"]
    seen = wide[wide.index <= asof]
    assert v_hist == pytest.approx(seen.tail(5).mean() - seen.tail(20).mean())
    assert v_full == pytest.approx(7.5)


def test_overlay_alpha_nan_before_enough_history(delivery_overlay):
    assert pd.isna(az.alpha_delivery_momentum(_px("KOTAKBANK.NS", delivery_overlay[:10])))


# ── 3 + 4: promotion log and data-driven tags ───────────────────────────────
def test_promotion_log_distinguishes_no_data_from_rejection():
    ic = pd.DataFrame([
        {"alpha": "good", "horizon": 5, "n_dates": 20, "mean_IC": 0.10,
         "std_IC": 0.1, "t_stat": 3.0, "hit_rate": 0.7},
        {"alpha": "iv_rank", "horizon": 5, "n_dates": 0, "mean_IC": np.nan,
         "std_IC": np.nan, "t_stat": np.nan, "hit_rate": np.nan},
    ])
    log = {r["alpha"]: r for r in ae.build_promotion_log(ic, {}, {}, 0.03, 2.0, 0.015)}
    assert log["iv_rank"]["decision"] == "no_data", \
        "an alpha with no IC was not tested — it must not read as 'rejected'"
    assert log["iv_rank"]["n_dates"] == 0


def test_evaluator_return_panels_keeps_default_shape():
    out = ae.evaluate_alphas(pd.DataFrame())
    assert isinstance(out, pd.DataFrame)
    ic, panels, fwd = ae.evaluate_alphas(pd.DataFrame(), return_panels=True)
    assert ic.empty and panels == {} and fwd == {}


def test_trade_plan_builder_writes_promotion_log():
    src = (ROOT / "trade_plan_builder.py").read_text(encoding="utf-8")
    assert "build_promotion_log(" in src and "ALPHA_PROMOTION_LOG_JSON.write_text" in src


def test_dashboard_reads_writer_schema_and_tags_from_data(tmp_path):
    import dashboard_html_builder as dhb
    (tmp_path / "alpha_promotion_log.json").write_text(json.dumps({
        "threshold_ic": 0.03, "threshold_tstat": 2.0, "candidates": [
            {"alpha": "drawdown_recovery", "horizon": 5, "standalone_ic": 0.10,
             "standalone_tstat": 3.2, "residual_ic": 0.02, "n_dates": 20,
             "decision": "promote", "reason": "ok"},
            {"alpha": "drawdown_recovery", "horizon": 10, "standalone_ic": 0.04,
             "standalone_tstat": 0.9, "residual_ic": 0.04, "n_dates": 19,
             "decision": "reject", "reason": "x"},
            {"alpha": "delivery_momentum", "horizon": 5, "standalone_ic": 0.012,
             "standalone_tstat": 0.8, "residual_ic": 0.01, "n_dates": 4,
             "decision": "reject", "reason": "x"},
            {"alpha": "iv_rank", "horizon": 5, "standalone_ic": None,
             "standalone_tstat": None, "residual_ic": None, "n_dates": 0,
             "decision": "no_data", "reason": "no feed data"},
        ]}), encoding="utf-8")
    ev = dhb._alpha_evidence_payload(tmp_path)
    rows = {r["alpha"]: r for r in ev["rows"]}
    assert len([r for r in ev["rows"] if r["alpha"] == "drawdown_recovery"]) == 1, \
        "one row per alpha, not one per horizon"
    assert rows["drawdown_recovery"]["promote"] is True
    assert rows["drawdown_recovery"]["tstat"] == pytest.approx(3.2)
    assert rows["delivery_momentum"]["tag"] == "CANDIDATE"
    assert rows["iv_rank"]["tag"] == "NO DATA"
    assert ev["rows"][-1]["alpha"] == "iv_rank", "no-data rows sink to the bottom"

    # The render side must consume the payload's tag. (Deliberately NOT a
    # "banned literal is absent" check: the builder's own comment explaining
    # the old hard-coded list quotes it, and such a test can never pass.)
    src = (ROOT / "dashboard_html_builder.py").read_text(encoding="utf-8")
    assert "tagHtml(r.tag)" in src


# ── 5: maturity reads the pending file ──────────────────────────────────────
def test_awaiting_maturation_is_read_from_missing_signals(tmp_path, monkeypatch):
    import dashboard_html_builder as dhb
    pd.DataFrame({"Horizon_Days": [10] * 3, "Net_Forward_Return": [0.01, 0.02, -0.01]}) \
        .to_csv(tmp_path / "forward_return_history.csv", index=False)
    pd.DataFrame({"Horizon_Days": [10, 10], "Reason": ["Forward horizon not matured yet"] * 2}) \
        .to_csv(tmp_path / "forward_return_missing_signals.csv", index=False)
    monkeypatch.setattr(dhb, "OUT", tmp_path)
    P = dhb._payload()
    assert P["maturity"]["maturing"] == 2, "pending lives in the missing-signals file"
    assert P["maturity"]["matured"] == 3
    assert P["maturity"]["rate"] == pytest.approx(60.0)


# ── 6: macro tape ───────────────────────────────────────────────────────────
def _tape(n=300):
    d = pd.bdate_range("2025-08-01", periods=n)
    return pd.concat([
        pd.DataFrame({"Date": d, "Symbol": "^NSEI", "Close": np.linspace(20000, 26000, n)}),
        pd.DataFrame({"Date": d, "Symbol": "^INDIAVIX", "Close": np.linspace(18, 11, n)}),
    ], ignore_index=True)


def test_macro_tape_flags_default_vs_measured():
    blind = sent.macro_tape_score(pd.DataFrame({"Date": ["2026-10-12"], "Symbol": ["X.NS"],
                                                "Close": [1.0]}))
    assert blind["regime"] == "neutral" and blind["regime_inputs_available"] is False
    seen = sent.macro_tape_score(_tape())
    assert seen["regime_inputs_available"] is True
    assert seen["vix_level"] is not None and seen["nifty_50d_trend"] is not None


def test_index_tape_cache_never_shrinks_and_survives_offline(tmp_path):
    _tape().to_csv(tmp_path / sent.INDEX_CACHE_NAME, index=False)
    got = sent.load_index_tape(tmp_path, fetch=False)      # offline: cache only
    assert len(got) == 600
    assert set(got["Symbol"]) == {"^NSEI", "^INDIAVIX"}


def test_step4_reads_index_tape_not_candidate_prices():
    src = (ROOT / "trade_plan_builder.py").read_text(encoding="utf-8")
    assert "load_index_tape(" in src
