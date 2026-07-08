"""Unit tests for the new core modules (regime, sector_context, etf_microstructure,
data_quality) and the orchestrator wiring. Pure logic — no network."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import regime, sector_context, etf_microstructure as micro, data_quality as dq, alpha_zoo

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


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print("\nALL NEW-MODULE TESTS PASSED")
