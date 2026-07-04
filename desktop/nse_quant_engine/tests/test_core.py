"""
Unit tests for the clean-core pure-logic modules.

These prove the SCORING, FUNDAMENTAL, EXPECTED-VALUE, VALIDATION-STATUS, and
PRICE-CACHE logic behave correctly on synthetic data — the parts that decide
investment outcomes, which is exactly where a silent error costs money.

Run:  python -m pytest tests/ -v     (or)     python tests/test_core.py
"""
import sys, json
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import scoring, fundamental_factor, expected_value, validation_status, price_cache, config as C


def _ok(name): print(f"  PASS: {name}")


def test_momentum_not_triple_counted():
    """A calm up-trend should beat a violent up-trend with the SAME raw return
    (risk-adjusted momentum), proving we reward return-per-risk, not raw return."""
    df = pd.DataFrame([
        {"Symbol": "CALM",    "Return_5D": .02, "Return_21D": .06, "Return_63D": .10,
         "Volatility_20D": .12, "Price": 110, "MA50": 100, "MA200": 90, "Bench_Return_21D": .03,
         "Drawdown_60D": -.01, "RSI": 55},
        {"Symbol": "VIOLENT", "Return_5D": .02, "Return_21D": .06, "Return_63D": .10,
         "Volatility_20D": .45, "Price": 110, "MA50": 100, "MA200": 90, "Bench_Return_21D": .03,
         "Drawdown_60D": -.01, "RSI": 55},
    ])
    res = scoring.compute_opportunity_scores(df).set_index("Symbol")
    assert res.loc["CALM", "Opportunity_Score"] > res.loc["VIOLENT", "Opportunity_Score"]
    _ok("risk-adjusted momentum rewards calm over violent (same raw return)")


def test_absolute_filter_caps_falling_knife():
    """A name with negative 21D return cannot be a 'Top Candidate' even if it's
    the least-bad in a falling universe."""
    df = pd.DataFrame([
        {"Symbol": "FALLER", "Return_5D": -.01, "Return_21D": -.03, "Return_63D": -.05,
         "Volatility_20D": .15, "Price": 90, "MA50": 100, "MA200": 110, "Bench_Return_21D": -.08,
         "Drawdown_60D": -.10, "RSI": 40},
    ])
    res = scoring.compute_opportunity_scores(df).set_index("Symbol")
    assert res.loc["FALLER", "Opportunity_Score"] <= 50.0
    _ok("absolute filter caps negative-21D 'falling knife'")


def test_overbought_penalised():
    base = {"Return_5D": .02, "Return_21D": .06, "Return_63D": .10, "Volatility_20D": .15,
            "Price": 110, "MA50": 100, "MA200": 90, "Bench_Return_21D": .03, "Drawdown_60D": -.01}
    df = pd.DataFrame([{**base, "Symbol": "CALM_RSI", "RSI": 55},
                       {**base, "Symbol": "HOT_RSI", "RSI": 82}])
    res = scoring.compute_opportunity_scores(df).set_index("Symbol")
    assert res.loc["CALM_RSI", "Opportunity_Score"] > res.loc["HOT_RSI", "Opportunity_Score"]
    _ok("overbought RSI penalised vs calm RSI")


def test_fundamental_factor_direction():
    """High ROE / low PE / low debt should score higher than the opposite."""
    fund = pd.DataFrame([
        {"Symbol": "QUALITY", "PE": 15, "ROE": .25, "DebtToEquity": 20, "EarningsGrowth": .18, "ProfitMargin": .22},
        {"Symbol": "JUNK",    "PE": 80, "ROE": .02, "DebtToEquity": 250, "EarningsGrowth": -.10, "ProfitMargin": .01},
        {"Symbol": "MID",     "PE": 30, "ROE": .12, "DebtToEquity": 90, "EarningsGrowth": .05, "ProfitMargin": .10},
    ])
    res = fundamental_factor.build_quality_score(fund).set_index("Symbol")
    assert res.loc["QUALITY", "Fundamental_Score"] > res.loc["JUNK", "Fundamental_Score"]
    _ok("fundamental factor: quality > junk")


def test_fundamental_weight_zero_disables():
    df = pd.DataFrame([{"Symbol": "X", "Opportunity_Score": 80.0, "Fundamental_Score": 10.0, "Universe": "Stock"}])
    old = C.FUNDAMENTAL_WEIGHT
    try:
        C.FUNDAMENTAL_WEIGHT = 0.0
        res = scoring.apply_fundamental_factor(df)
        assert abs(res.loc[0, "Final_Score"] - 80.0) < 1e-9
        _ok("fundamental weight=0 leaves score unchanged")
    finally:
        C.FUNDAMENTAL_WEIGHT = old


def test_low_fundamental_coverage_skips_adjustment():
    df = pd.DataFrame([{"Symbol": "X", "Opportunity_Score": 80.0, "Fundamental_Score": 10.0, "Fundamental_Coverage": 0.20, "Universe": "Stock"}])
    old_w, old_cov = C.FUNDAMENTAL_WEIGHT, C.FUNDAMENTAL_MIN_COVERAGE
    try:
        C.FUNDAMENTAL_WEIGHT = 0.30
        C.FUNDAMENTAL_MIN_COVERAGE = 0.60
        res = scoring.apply_fundamental_factor(df)
        assert abs(res.loc[0, "Final_Score"] - 80.0) < 1e-9
        _ok("low fundamental coverage does not alter score")
    finally:
        C.FUNDAMENTAL_WEIGHT, C.FUNDAMENTAL_MIN_COVERAGE = old_w, old_cov


def test_etf_skips_fundamentals():
    df = pd.DataFrame([{"Symbol": "ETF1", "Opportunity_Score": 70.0, "Fundamental_Score": 5.0, "Universe": "ETF"}])
    old = C.FUNDAMENTAL_WEIGHT
    try:
        C.FUNDAMENTAL_WEIGHT = 0.30
        res = scoring.apply_fundamental_factor(df)
        assert abs(res.loc[0, "Final_Score"] - 70.0) < 1e-9  # unchanged for ETF
        _ok("ETFs skip fundamental adjustment (neutral)")
    finally:
        C.FUNDAMENTAL_WEIGHT = old


def test_ev_blank_until_validated():
    fwd = pd.DataFrame({"Horizon_Days": [10] * 100,
                        "Net_Forward_Return": np.random.uniform(-.05, .05, 100)})
    not_validated = {"verdict": "Insufficient History"}
    res = expected_value.expected_value_per_day(fwd, not_validated, horizon=10)
    assert np.isnan(res["ev_per_day"])
    assert "not positive" in res["status"].lower()
    _ok("EV stays blank until validation positive")


def test_ev_computes_when_validated():
    # 60% win @ +3%, 40% loss @ -2%  -> EV/trade = .6*.03 - .4*.02 = .010
    rets = [.03] * 60 + [-.02] * 40
    fwd = pd.DataFrame({"Horizon_Days": [10] * 100, "Net_Forward_Return": rets, "Score_Bucket": ["TOP"] * 100})
    validated = {"verdict": "Validation Positive"}
    res = expected_value.expected_value_per_day(fwd, validated, horizon=10, hold_days=10, filters={"Score_Bucket": "TOP"})
    assert res["n_obs"] == 100
    assert abs(res["p_win"] - 0.60) < 1e-6
    assert res["ev_per_trade"] > 0
    assert abs(res["ev_per_day"] - res["ev_per_trade"] / 10) < 1e-9
    _ok("EV computes correctly from validated filtered forward returns")


def test_ev_rejects_thin_filtered_sample():
    fwd = pd.DataFrame({"Horizon_Days": [10] * 60, "Net_Forward_Return": [.02] * 60, "Score_Bucket": ["TOP"] * 20 + ["OTHER"] * 40})
    validated = {"verdict": "Validation Positive"}
    res = expected_value.expected_value_per_day(fwd, validated, horizon=10, filters={"Score_Bucket": "TOP"}, min_obs=50)
    assert np.isnan(res["ev_per_day"])
    assert res["n_obs"] == 20
    _ok("EV refuses thin filtered sample")


def test_verdict_gates():
    # Too few dates -> Insufficient History regardless of great stats
    s1 = {"validation_dates": 3, "effective_validation_dates": 2, "avg_obs": 300,
          "spread": .02, "hit_rate": .8, "adj_tstat": 3.0, "bootstrap_prob": .99}
    assert validation_status.decide_verdict(s1)[0] == "Insufficient History"
    # Enough data but negative spread -> Validation Negative
    s2 = {"validation_dates": 12, "effective_validation_dates": 8, "avg_obs": 300,
          "spread": -.01, "hit_rate": .45, "adj_tstat": -1.0, "bootstrap_prob": .2}
    assert validation_status.decide_verdict(s2)[0] == "Validation Negative"
    # All gates clear -> Validation Positive
    s3 = {"validation_dates": 12, "effective_validation_dates": 8, "avg_obs": 300,
          "spread": .01, "hit_rate": .6, "adj_tstat": 2.0, "bootstrap_prob": .95}
    assert validation_status.decide_verdict(s3)[0] == "Validation Positive"
    _ok("verdict gates: date floor, negative-spread, full-pass all correct")


def test_status_roundtrip_and_failsafe(tmp_path=None):
    import tempfile
    d = Path(tempfile.mkdtemp())
    f = d / "validation_status.json"
    stats = {"validation_dates": 12, "spread": .01}
    validation_status.write_status(f, "Validation Positive", "Sufficient Evidence", stats)
    back = validation_status.read_status(f)
    assert back["verdict"] == "Validation Positive"
    # missing file -> fail safe to Insufficient History
    missing = validation_status.read_status(d / "nope.json")
    assert missing["verdict"] == "Insufficient History"
    _ok("status JSON round-trips; missing file fails safe to watchlist-only")


def test_price_cache_freezes_history():
    """The core correctness guarantee: an existing price is NOT overwritten when
    fresh data re-adjusts it — only genuinely new dates are appended."""
    cache = pd.DataFrame({"Date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
                          "Symbol": ["AAA", "AAA"], "Price": [100.0, 101.0]})
    # yfinance re-adjusts: same dates come back DIFFERENT, plus one new date
    fresh = pd.DataFrame({"Date": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"]),
                          "Symbol": ["AAA", "AAA", "AAA"], "Price": [95.0, 96.0, 102.0]})
    merged = price_cache.update_cache(cache, fresh, freeze_history=True)
    m = merged.set_index("Date")["Price"]
    assert m[pd.Timestamp("2026-01-01")] == 100.0   # NOT overwritten to 95
    assert m[pd.Timestamp("2026-01-02")] == 101.0   # NOT overwritten to 96
    assert m[pd.Timestamp("2026-01-03")] == 102.0   # new date appended
    _ok("price cache freezes history (adjustment-drift protection)")


def test_cache_needs_download_detection():
    cache = pd.DataFrame({"Date": pd.to_datetime(["2026-06-20"]), "Symbol": ["AAA"], "Price": [100.0]})
    today = pd.Timestamp("2026-06-23")
    needs = price_cache.symbols_needing_download(cache, ["AAA", "BBB"], today, max_stale_days=1)
    assert "BBB" in needs       # never seen
    assert "AAA" in needs       # 3 days stale > 1
    _ok("cache detects missing + stale symbols for selective download")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    print(f"Running {len(fns)} core logic tests...\n")
    failed = 0
    for fn in fns:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"  FAIL: {fn.__name__} -> {e}")
        except Exception as e:
            failed += 1
            print(f"  ERROR: {fn.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(fns)-failed}/{len(fns)} passed.")
    sys.exit(1 if failed else 0)
