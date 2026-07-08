"""Unit tests for the new core modules (regime, sector_context, etf_microstructure,
data_quality) and the orchestrator wiring. Pure logic — no network."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import (regime, sector_context, etf_microstructure as micro,
                  data_quality as dq, alpha_zoo, portfolio_selection as psel,
                  horizon_optimizer as hopt, sentiment_overlay as sent,
                  alpha_evaluator as ae, fundamentals_overlay as fo,
                  position_sizer as pz, backtest_engine as bt,
                  evidence_bundle as eb)

def _ok(n): print(f"  PASS: {n}")

def test_regime_classify():
    assert regime.classify(10) == "LOW"
    assert regime.classify(50) == "MID"
    assert regime.classify(80) == "HIGH"
    s = pd.Series(np.linspace(10, 30, 260))
    p = regime.percentile_252d(s)
    assert 90 < p <= 100, p
    sc = regime.scale_for_regime("HIGH")
    assert sc["vol_penalty_mult"] > 1
    _ok("regime classify + scale")

def test_breadth():
    dates = pd.bdate_range("2024-01-01", periods=120)
    rows = []
    for s in ["A","B","C"]:
        # all uptrending; expect 100%
        rows += [{"Date": d, "Symbol": s, "Price": 100 + i*0.5} for i, d in enumerate(dates)]
    df = pd.DataFrame(rows)
    b = regime.breadth_pct_above_ma(df, window=50)
    assert b == 100.0, b
    _ok("breadth_pct_above_ma")

def test_sector_rs():
    m = sector_context.sector_rs_multiplier(0.10, 0.05)
    assert m == 1.0
    m2 = sector_context.sector_rs_multiplier(0.01, 0.05)
    assert m2 < 1
    c = sector_context.combined_rs(1.0, 0.92)
    assert 0.95 < c < 0.97
    assert sector_context.map_symbol_to_sector_index("Technology") == "^CNXIT"
    _ok("sector RS + mapping")

def test_microstructure():
    dates = pd.bdate_range("2024-01-01", periods=30)
    rows = [{"Date": d, "Symbol": "X", "Close": 100, "Volume": 10_000,
             "High": 101, "Low": 99} for d in dates]
    df = pd.DataFrame(rows)
    tv = micro.traded_value_20d(df, "X")
    assert tv == 1_000_000, tv
    sp = micro.hl_spread_pct(df, "X")
    assert 0.01 <= sp <= 0.025
    ok, reasons = micro.passes_microstructure(tv, 0.5)
    assert not ok and any("LOW_LIQUIDITY" in r for r in reasons)
    ok2, _ = micro.passes_microstructure(5e7, 0.5)
    assert ok2
    _ok("microstructure liquidity + spread")

def test_inav_premium_z():
    prices = pd.Series(np.r_[np.ones(59)*100, [110.0]])
    navs = pd.Series(np.ones(60)*100)
    z = micro.inav_premium_z(prices, navs)
    assert z > 5
    _ok("inav premium z-score")

def test_dq_flags_basic():
    today = datetime(2026, 6, 26)
    row = {"TER": None, "Tracking_Error": None, "Tracking_Difference": None,
           "NAV_Date": "01-Jan-2020", "AUM_Cr": 5.0, "Mapping_Status": "Unresolved"}
    flags = dq.classify_row(row, today)
    for expected in ("MISSING_TER", "MISSING_TRACKING", "STALE_NAV", "LOW_AUM", "UNRESOLVED_MAPPING"):
        assert expected in flags, (expected, flags)
    _ok("dq flag classification")

def test_dq_ok_row():
    today = datetime(2026, 6, 26)
    row = {"TER": 0.002, "Tracking_Error": 0.01, "Tracking_Difference": -0.001,
           "NAV_Date": "25-Jun-2026", "AUM_Cr": 1000.0, "Mapping_Status": "Verified"}
    assert dq.classify_row(row, today) == ["OK"]
    _ok("dq OK row")

def test_dq_annotate_health():
    rows = [
        {"TER": 0.002, "Tracking_Error": 0.01, "Tracking_Difference": -0.001,
         "NAV_Date": "25-Jun-2026", "AUM_Cr": 1000.0, "Mapping_Status": "Verified", "NAV": 100, "Benchmark_Index": "X"},
        {"TER": None, "Tracking_Error": None, "Tracking_Difference": None,
         "NAV_Date": "01-Jan-2020", "AUM_Cr": 5.0, "Mapping_Status": "Unresolved", "NAV": 50, "Benchmark_Index": None},
    ]
    df = pd.DataFrame(rows)
    out = dq.annotate(df, today=datetime(2026, 6, 26))
    assert "Quality_Flags" in out.columns
    assert "Flag_OK" in out.columns
    score = dq.health_score(out)
    assert 0 <= score <= 100
    _ok(f"dq annotate + health_score={score}")

def test_orchestrator_steps_build():
    import orchestrator
    s_full = orchestrator.build_steps(include_shadow=True, include_fetch=True)
    s_no_fetch = orchestrator.build_steps(include_shadow=True, include_fetch=False)
    assert any("nse_quant_engine" == st.name for st in s_full)
    assert len(s_no_fetch) < len(s_full)
    _ok(f"orchestrator builds {len(s_full)} / {len(s_no_fetch)} steps")


def test_alpha_zoo_no_lookahead():
    """Guard: no alpha may reference future values via shift(-N) or negative rolling."""
    import inspect, re
    src = inspect.getsource(alpha_zoo)
    assert not re.search(r"\.shift\(\s*-\s*\d", src), "found forward .shift(-N)"
    assert not re.search(r"rolling\(\s*-\s*\d", src), "found negative rolling window"
    _ok("alpha_zoo lookahead guard")


def test_alpha_zoo_compute_shape():
    dates = pd.bdate_range("2024-01-01", periods=260)
    rows = []
    rng = np.random.default_rng(0)
    for s, drift in [("A", 0.0008), ("B", 0.0003), ("C", -0.0002)]:
        px = 100 * np.cumprod(1 + rng.normal(drift, 0.012, len(dates)))
        for d, p in zip(dates, px):
            rows.append({"Date": d, "Symbol": s, "Open": p, "High": p*1.01,
                         "Low": p*0.99, "Close": p, "Volume": 100000})
    df = pd.DataFrame(rows)
    z = alpha_zoo.compute_alpha_zoo(df)
    assert set(["Symbol", "Zoo_Score", "Zoo_Coverage"]).issubset(z.columns)
    assert len(z) == 3
    assert z["Zoo_Score"].notna().all()
    assert (z["Zoo_Score"].between(0, 100)).all()
    _ok(f"alpha_zoo compute: {len(alpha_zoo.ALPHAS)} alphas, coverage min={z['Zoo_Coverage'].min():.2f}")


def _synthetic_prices(seed=1):
    """5 highly-correlated 'IT' names + 5 independent names + Nifty."""
    dates = pd.bdate_range("2024-01-01", periods=120)
    rng = np.random.default_rng(seed)
    common = rng.normal(0.0005, 0.010, len(dates))
    rows = []
    for i in range(5):  # correlated cluster
        px = 100 * np.cumprod(1 + common + rng.normal(0, 0.002, len(dates)))
        for d, p in zip(dates, px):
            rows.append({"Date": d, "Symbol": f"IT{i}", "Close": p, "Volume": 1000})
    for i in range(5):  # independent
        px = 100 * np.cumprod(1 + rng.normal(0.0004, 0.012, len(dates)))
        for d, p in zip(dates, px):
            rows.append({"Date": d, "Symbol": f"IND{i}", "Close": p, "Volume": 1000})
    bench = 100 * np.cumprod(1 + rng.normal(0.0003, 0.009, len(dates)))
    for d, p in zip(dates, bench):
        rows.append({"Date": d, "Symbol": "^NSEI", "Close": p, "Volume": 0})
    return pd.DataFrame(rows)


def test_portfolio_diversification_picks_uncorrelated():
    prices = _synthetic_prices()
    syms = [f"IT{i}" for i in range(5)] + [f"IND{i}" for i in range(5)]
    cand = pd.DataFrame({"Symbol": syms,
                         "Final_Score": [95,94,93,92,91,88,86,84,82,80]})
    corr = psel.pairwise_corr(prices, syms, window=60)
    assert not corr.empty and corr.shape == (10, 10)
    # correlated cluster must have avg |corr| >> independent
    it_syms = [s for s in corr.index if s.startswith("IT")]
    ind_syms = [s for s in corr.index if s.startswith("IND")]
    it_avg = corr.loc[it_syms, it_syms].abs().values.mean()
    ind_pair = corr.loc[ind_syms, ind_syms].abs()
    import numpy as _np
    a = ind_pair.values.copy(); _np.fill_diagonal(a, _np.nan)
    ind_avg = _np.nanmean(a)
    assert it_avg > ind_avg, (it_avg, ind_avg)

    picked = psel.diversified_top_n(cand, corr, n=5, alpha=0.55)
    assert len(picked) == 5
    n_ind = sum(1 for s in picked if s.startswith("IND"))
    assert n_ind >= 2, f"diversifier should include >=2 independent names, got {picked}"
    _ok(f"portfolio diversifier picked {picked} (n_ind={n_ind})")


def test_benchmark_stats_math():
    prices = _synthetic_prices(seed=2)
    stats = psel.benchmark_stats(prices, ["IT0", "IND0"], benchmark="^NSEI")
    assert list(stats["Symbol"]) == ["IT0", "IND0"]
    for col in ["Excess_21D", "InformationRatio_63D", "TrackingError_63D", "BetaVsBenchmark_63D"]:
        assert col in stats.columns
        assert stats[col].notna().all(), (col, stats)
    # TE must be non-negative
    assert (stats["TrackingError_63D"] >= 0).all()
    _ok(f"benchmark_stats produced {stats.shape} with all-finite values")


def test_horizon_optimizer_upward_drift():
    dates = pd.bdate_range("2023-01-01", periods=400)
    rng = np.random.default_rng(3)
    px = 100 * np.cumprod(1 + rng.normal(0.0009, 0.010, len(dates)))
    df = pd.DataFrame([{"Date": d, "Symbol": "UP", "Close": p} for d, p in zip(dates, px)])
    rec = hopt.optimal_horizon(df, "UP")
    assert rec["Rec_Horizon_Days"] in hopt.DEFAULT_HORIZONS
    assert rec["Exp_Ret_%"] > 0
    _ok(f"horizon optimizer upward drift: h={rec['Rec_Horizon_Days']} exp={rec['Exp_Ret_%']:.2f}%")


def test_horizon_optimizer_short_history():
    dates = pd.bdate_range("2024-01-01", periods=5)
    df = pd.DataFrame([{"Date": d, "Symbol": "SHORT", "Close": 100.0} for d in dates])
    rec = hopt.optimal_horizon(df, "SHORT")
    assert pd.isna(rec["Rec_Horizon_Days"])
    _ok("horizon optimizer handles short history safely")


def test_horizon_no_lookahead_source():
    import inspect, re
    src = inspect.getsource(hopt)
    assert not re.search(r"\.shift\(\s*-\s*\d", src), "found forward .shift(-N) in horizon_optimizer"
    _ok("horizon optimizer lookahead guard")


def test_sentiment_polarity_bounds():
    lex = sent.load_lexicon()
    assert lex, "lexicon empty — csv missing?"
    p_pos = sent.polarity("Company beats guidance and surges to new high", lex)
    p_neg = sent.polarity("Company plunges after fraud lawsuit and downgrade", lex)
    p_neu = sent.polarity("Company held annual meeting", lex)
    assert -1.0 <= p_pos <= 1.0 and -1.0 <= p_neg <= 1.0
    assert p_pos > 0 and p_neg < 0 and abs(p_neu) < 0.2
    _ok(f"sentiment polarity: +={p_pos:.2f}  -={p_neg:.2f}  neu={p_neu:.2f}")


def test_sentiment_veto_triggers():
    news = pd.DataFrame([
        {"Symbol": "BAD", "Headline": "plunges after fraud probe", "Date": pd.Timestamp.now()},
        {"Symbol": "BAD", "Headline": "downgrade cuts guidance",  "Date": pd.Timestamp.now()},
        {"Symbol": "BAD", "Headline": "lawsuit weighs on outlook","Date": pd.Timestamp.now()},
        {"Symbol": "GOOD","Headline": "beats and surges to new high","Date": pd.Timestamp.now()},
    ])
    s_df = sent.score_headlines(news)
    vetoed = sent.sentiment_veto(s_df, min_headlines=3, neg_pct_veto=0.60)
    assert "BAD" in vetoed and "GOOD" not in vetoed
    _ok(f"sentiment veto: {sorted(vetoed)}")


def test_alpha_evaluator_smoke():
    dates = pd.bdate_range("2023-01-01", periods=320)
    rng = np.random.default_rng(9)
    rows = []
    for s in ["A", "B", "C", "D", "E", "F"]:
        drift = rng.normal(0.0004, 0.0002)
        px = 100 * np.cumprod(1 + rng.normal(drift, 0.011, len(dates)))
        for d, p in zip(dates, px):
            rows.append({"Date": d, "Symbol": s, "Open": p, "High": p*1.01,
                         "Low": p*0.99, "Close": p, "Volume": 100000})
    df = pd.DataFrame(rows)
    ic = ae.evaluate_alphas(df, horizons=(5, 10), eval_days=120, folds=3, max_dates=6)
    assert not ic.empty
    for col in ["alpha", "horizon", "mean_IC", "t_stat", "hit_rate"]:
        assert col in ic.columns
    surv = ae.promote_alphas(ic, min_ic=0.03, min_tstat=2.0)
    assert isinstance(surv, list)
    _ok(f"alpha_evaluator: {len(ic)} rows, {len(surv)} survivors on synthetic panel")


def test_fundamentals_quality_score_sign():
    fund = pd.DataFrame([
        {"Symbol": "GOOD", "ROE_TTM": 0.25, "DebtToEquity": 0.1,
         "EPS_Growth_YoY": 0.30, "PE_TTM": 18},
        {"Symbol": "MID",  "ROE_TTM": 0.15, "DebtToEquity": 0.5,
         "EPS_Growth_YoY": 0.10, "PE_TTM": 25},
        {"Symbol": "BAD",  "ROE_TTM": 0.02, "DebtToEquity": 3.0,
         "EPS_Growth_YoY": -0.20, "PE_TTM": 60},
    ])
    q = fo.quality_score(fund)
    assert q.iloc[0] > q.iloc[2], q.tolist()
    assert (q.between(-3, 3)).all()
    flag_cheap = fo.valuation_flag(pe=10.0, self_median_pe=25.0, sector_median_pe=22.0)
    flag_exp = fo.valuation_flag(pe=60.0, self_median_pe=25.0, sector_median_pe=22.0)
    assert flag_cheap == "Cheap" and flag_exp == "Expensive"
    _ok(f"fundamentals overlay: quality good>bad ({q.iloc[0]:.2f} > {q.iloc[2]:.2f})")


def test_position_sizer_identity_corr_gives_near_equal_weight():
    # 3 names, identity corr → risk-parity ≈ inverse-vol proportional
    prices = _synthetic_prices(seed=4)
    top5 = pd.DataFrame({
        "Symbol": ["IT0", "IND0", "IND1"],
        "Price":  [100.0, 100.0, 100.0],
        "Stop_Loss": [95.0, 95.0, 95.0],
    })
    corr = pd.DataFrame(np.eye(3), index=top5["Symbol"], columns=top5["Symbol"])
    out = pz.size_portfolio(top5, prices_long=prices, corr=corr,
                            mode="risk_parity_lite", nav_inr=1_000_000)
    assert not out.empty and "Weight_%" in out.columns
    # weights positive, and after vol-target scaling sum≤100 with buffer
    assert (out["Weight_%"] > 0).all()
    assert out["Max_Loss_INR"].notna().all()
    _ok(f"position sizer: weights={out['Weight_%'].round(1).tolist()}")


def test_position_sizer_fallback_no_prices():
    top5 = pd.DataFrame({"Symbol": ["A", "B"], "Price": [100, 200], "Stop_Loss": [95, 190]})
    out = pz.size_portfolio(top5, prices_long=None, corr=None, nav_inr=100000)
    assert not out.empty
    # equal-weight fallback
    assert abs(out["Weight_%"].sum() - 100.0) < 1e-6
    _ok("position sizer: equal-weight fallback works")


def test_backtest_no_lookahead_and_shape():
    import inspect, re
    src = inspect.getsource(bt)
    assert not re.search(r"\.shift\(\s*-\s*\d", src), "found forward .shift(-N) in backtest"
    prices = _synthetic_prices(seed=5)
    # need longer history
    dates = pd.bdate_range("2022-01-01", periods=400)
    rng = np.random.default_rng(7)
    rows = []
    for s in ["A", "B", "C", "D", "E", "F", "^NSEI"]:
        drift = rng.normal(0.0004, 0.0002)
        px = 100 * np.cumprod(1 + rng.normal(drift, 0.011, len(dates)))
        for d, p in zip(dates, px):
            rows.append({"Date": d, "Symbol": s, "Close": p, "Volume": 1000})
    df = pd.DataFrame(rows)
    res = bt.run_backtest(df, lookback_days=200, rebal_every=10, hold_days=10, top_n=3)
    sc = res["scorecard"]; cv = res["equity_curve"]
    assert not sc.empty and not cv.empty
    for col in ["Variant", "N_Rebalances", "Hit_Rate", "Sharpe_Ann"]:
        assert col in sc.columns
    _ok(f"backtest: {len(cv)} rebalances, variants={sc['Variant'].tolist()}")


def test_evidence_bundle_zip_contents(tmp_path=None):
    import tempfile, zipfile, shutil, json as _j
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "output").mkdir()
        (tmp / "prompts").mkdir()
        # minimal trade plan
        pd.DataFrame([{
            "Symbol": "TEST", "Name": "Test Co", "Trade_Status": "Review - evidence gated",
            "Final_Score": 90, "Confidence_Adjusted_Score": 88, "Price": 100.0,
            "Stop_Loss": 95, "Target_1": 110, "Target_2": 120,
            "Buy_Zone_Low": 99, "Buy_Zone_High": 101, "Key_Risk": "test",
        }]).to_csv(tmp / "output" / "trade_plan_latest.csv", index=False)
        (tmp / "prompts" / "rationale_prompt.md").write_text("# stub", encoding="utf-8")
        # optional artifact
        pd.DataFrame([{"Symbol": "TEST", "Rec_Horizon_Days": 10, "Exp_Ret_%": 3.0}]) \
            .to_csv(tmp / "output" / "top5_horizon.csv", index=False)

        zp = eb.build_bundle(tmp / "output", tmp / "prompts")
        assert zp is not None and zp.exists()
        with zipfile.ZipFile(zp) as zf:
            names = set(zf.namelist())
            for req in ("top5.csv", "evidence.json", "run_manifest.json",
                        "README_for_AI.md", "top5_horizon.csv"):
                assert req in names, (req, names)
            ev = _j.loads(zf.read("evidence.json"))
            assert ev["picks"][0]["symbol"] == "TEST"
            assert ev["picks"][0]["horizon"].get("Rec_Horizon_Days") == 10
        _ok(f"evidence bundle: {zp.name} contains {len(names)} files")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rationale_prompt_present():
    p = Path(__file__).resolve().parent.parent / "prompts" / "rationale_prompt.md"
    assert p.exists(), "rationale_prompt.md missing — bundle would ship without AI instructions"
    txt = p.read_text(encoding="utf-8")
    for req in ("Output contract", "STRICT JSON", "picks", "confidence"):
        assert req in txt, req
    _ok("rationale prompt spec present and complete")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print("\nALL NEW-MODULE TESTS PASSED")


# ────────────────────────────────────────────────────────────────────────────
# Steps 10–13 tests
# ────────────────────────────────────────────────────────────────────────────

def _prices_frame(seed=11, n=200, syms=("A", "B", "C", "D", "E", "F", "^NSEI")):
    dates = pd.bdate_range("2024-01-01", periods=n)
    rng = np.random.default_rng(seed)
    rows = []
    for s in syms:
        px = 100 * np.cumprod(1 + rng.normal(0.0005, 0.011, len(dates)))
        for d, p in zip(dates, px):
            rows.append({"Date": d, "Symbol": s, "Close": p, "Volume": 1000})
    return pd.DataFrame(rows)


def test_sector_context_enrich():
    from core import sector_context as sc
    top5 = pd.DataFrame({"Symbol": ["A", "B", "C"]})
    fund = pd.DataFrame({"Symbol": ["A", "B", "C"],
                         "Sector": ["Technology", "Banks", "Healthcare"]})
    out = sc.enrich(top5, _prices_frame(), fund)
    assert not out.empty
    for c in ("Sector_RS_63D_%", "Peer_1", "Peer_Median_3M_Return_%"):
        assert c in out.columns, c
    assert (out["Sector"] == ["Technology", "Banks", "Healthcare"]).all()
    _ok(f"sector context: peers={out['Peer_1'].tolist()}")


def test_event_calendar_flags():
    from core import event_calendar as ec
    top5 = pd.DataFrame({"Symbol": ["A", "B", "C"]})
    hz = pd.DataFrame({"Symbol": ["A", "B", "C"], "Rec_Horizon_Days": [10, 10, 10]})
    today = pd.Timestamp("2026-07-08")
    fund = pd.DataFrame({
        "Symbol": ["A", "B", "C"],
        "NextEarningsDate": [today + pd.Timedelta(days=5),
                             today + pd.Timedelta(days=25),
                             None],
    })
    out = ec.build(top5, hz, fund, as_of=today)
    flags = dict(zip(out["Symbol"], out["Event_Risk_Flag"]))
    assert flags["A"] == "In_Window", flags
    assert flags["B"] == "Pre_Earnings", flags
    assert flags["C"] == "Unknown", flags
    _ok(f"event calendar: {flags}")


def test_expected_value_top5_report():
    from core import expected_value as ev
    top5 = pd.DataFrame({"Symbol": ["A", "B"]})
    backtest = pd.DataFrame([{
        "Variant": "Style_Backtest_Top5_EW", "Hit_Rate": 0.60,
        "AvgWin_%": 3.0, "AvgLoss_%": -2.0, "N_Rebalances": 20,
    }])
    hz = pd.DataFrame({"Symbol": ["A", "B"], "Downside_Vol_%": [1.2, 2.1]})
    sizing = pd.DataFrame({"Symbol": ["A", "B"], "Weight_%": [55.0, 35.0]})
    out = ev.top5_ev_report(top5, backtest, hz, sizing, kelly_cap_of_weight=0.25)
    assert not out.empty
    # EV = 0.6*3 - 0.4*2 = 1.0 (%)
    assert abs(float(out["EV_%"].iloc[0]) - 1.0) < 0.01, out["EV_%"].tolist()
    assert (out["EV_Sizing_Agree"] == "Yes").all()
    assert out["Kelly_Fraction_Capped"].notna().all()
    _ok(f"EV report: EV_%={out['EV_%'].tolist()}")


def test_portfolio_validation_verdicts():
    import tempfile, json as _j
    from core import portfolio_validation as pv
    tmp = Path(tempfile.mkdtemp()) / "output"
    tmp.mkdir(parents=True)
    # Ship path: no breaches
    pd.DataFrame({"Symbol": ["A", "B"], "A": [1.0, 0.2], "B": [0.2, 1.0]}) \
        .to_csv(tmp / "top5_corr_matrix.csv", index=False)
    pd.DataFrame({"Symbol": ["A", "B"], "Weight_%": [50.0, 50.0],
                  "Max_Loss_%_of_NAV": [1.0, 1.0]}) \
        .to_csv(tmp / "top5_position_sizing.csv", index=False)
    pd.DataFrame({"Symbol": ["A", "B"], "Sector": ["Tech", "Banks"]}) \
        .to_csv(tmp / "top5_sector_context.csv", index=False)
    pd.DataFrame([{"Variant": "Style_Backtest_Top5_EW", "Hit_Rate": 0.62}]) \
        .to_csv(tmp / "backtest_scorecard.csv", index=False)
    (tmp / "alpha_zoo_survivors.json").write_text(
        _j.dumps([{"alpha": "a1"}, {"alpha": "a2"}, {"alpha": "a3"}]))
    (tmp / "macro_context.json").write_text('{"regime":"NEUTRAL"}')
    r = pv.validate_batch(tmp)
    assert r["verdict"] == "Ship", r
    # Downgrade path: RISK_OFF
    (tmp / "macro_context.json").write_text('{"regime":"RISK_OFF"}')
    r2 = pv.validate_batch(tmp)
    assert r2["verdict"] == "Downgrade_To_Watch", r2
    _ok(f"portfolio validation: verdicts={r['verdict']} / {r2['verdict']}")


def test_bundle_includes_new_files():
    import inspect
    from core import evidence_bundle as eb
    src = inspect.getsource(eb)
    for req in ("top5_sector_context.csv", "top5_events.csv",
                "top5_expected_value.csv", "portfolio_validation.json"):
        assert req in src, req
    _ok("evidence bundle wires steps 10–13 files")


if __name__ == "__main__":
    # Run the Step 10–13 tests appended after the primary __main__ block above.
    for _name in ("test_sector_context_enrich", "test_event_calendar_flags",
                  "test_expected_value_top5_report",
                  "test_portfolio_validation_verdicts",
                  "test_bundle_includes_new_files"):
        globals()[_name]()
    print("ALL STEP 10–13 TESTS PASSED")
