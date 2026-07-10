"""Tests for the fixed NSE fetchers and data-health writer."""
import sys, json
from pathlib import Path
import pandas as pd
import pytest

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core.optional_data_fetchers import (  # noqa: E402
    parse_delivery_bhavcopy,
    _write_health_row,
    _health_load,
    _norm_header,
    fetch_iv_rank,
    fetch_delivery_pct,
)

FIX = BASE / "tests" / "fixtures"


def test_norm_header():
    assert _norm_header(" DELIV_PER ") == "delivper"
    assert _norm_header("%DlyQtToTradedQty") == "dlyqttotradedqty"
    assert _norm_header("TTL_TRD_QNTY") == "ttltrdqnty"


def test_delivery_pct_header_detection_current_format():
    txt = (FIX / "sec_bhavdata_full_current.csv").read_text()
    df = parse_delivery_bhavcopy(txt)
    # index/IX row filtered; 3 EQ rows remain
    assert set(df["Symbol"]) == {"RELIANCE", "TCS", "INFY"}
    reliance = df.loc[df["Symbol"] == "RELIANCE", "Delivery_Pct"].iloc[0]
    assert abs(reliance - 80.0) < 0.01


def test_delivery_pct_header_detection_legacy_format():
    txt = (FIX / "sec_bhavdata_full_legacy.csv").read_text()
    df = parse_delivery_bhavcopy(txt)
    assert set(df["Symbol"]) == {"RELIANCE", "TCS"}
    assert abs(df.loc[df["Symbol"] == "TCS", "Delivery_Pct"].iloc[0] - 62.5) < 0.01


def test_delivery_pct_missing_percent_falls_back_to_ratio():
    txt = (FIX / "sec_bhavdata_full_ratio.csv").read_text()
    df = parse_delivery_bhavcopy(txt)
    assert set(df["Symbol"]) == {"RELIANCE", "TCS"}
    # 750000 / 1000000 * 100 == 75
    assert abs(df.loc[df["Symbol"] == "RELIANCE", "Delivery_Pct"].iloc[0] - 75.0) < 0.01
    assert abs(df.loc[df["Symbol"] == "TCS", "Delivery_Pct"].iloc[0] - 50.0) < 0.01


def test_iv_rank_session_warmup_called(monkeypatch, tmp_path):
    """The IV-rank fetcher must warm cookies on nseindia.com and the option-chain
    page before ever hitting api/option-chain-equities. Simulate NSE returning
    empty payloads so the fetcher exits without writing."""
    urls: list[str] = []

    class DummyResp:
        status_code = 200
        def json(self):
            return {"records": {"underlyingValue": 0, "data": []}}
        def raise_for_status(self):
            return None

    class DummySession:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=15, headers=None):
            urls.append(url)
            return DummyResp()

    monkeypatch.setattr("core.optional_data_fetchers._nse_browser_session",
                        lambda: DummySession())
    # Seed a shortlist so the function actually runs
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "latest_scores.csv").write_text("Symbol\nRELIANCE\nTCS\n")
    data_dir = tmp_path / "data"

    fetch_iv_rank(data_dir, tmp_path, cap=2)

    joined = "|".join(urls)
    assert "www.nseindia.com/" in urls[0]
    assert any("option-chain" in u and "api/" not in u for u in urls[:5]), joined
    first_api = next(i for i, u in enumerate(urls) if "api/option-chain-equities" in u)
    first_warmup_page = next(i for i, u in enumerate(urls) if "option-chain" in u and "api/" not in u)
    assert first_warmup_page < first_api, joined


def test_cache_not_wiped_on_failure(monkeypatch, tmp_path):
    """A failed IV-rank fetch must NEVER overwrite the good cached CSV."""
    data_dir = tmp_path / "data"; data_dir.mkdir()
    target = data_dir / "iv_rank_daily.csv"
    original = "Date,Symbol,IV,IV_Rank\n2026-07-01,RELIANCE,25.0,42.0\n"
    target.write_text(original)
    # Force the freshness check to fail (make file "old")
    import os, time
    old_mtime = time.time() - 3600 * 48
    os.utime(target, (old_mtime, old_mtime))

    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "latest_scores.csv").write_text("Symbol\nRELIANCE\n")

    class DummyResp:
        status_code = 403
        def json(self):
            raise RuntimeError("blocked")
        def raise_for_status(self):
            raise RuntimeError("blocked")

    class DummySession:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=15, headers=None):
            return DummyResp()

    monkeypatch.setattr("core.optional_data_fetchers._nse_browser_session",
                        lambda: DummySession())
    fetch_iv_rank(data_dir, tmp_path, cap=1)
    assert target.read_text() == original


def test_health_row_write_and_load(tmp_path):
    _write_health_row(tmp_path, "delivery_pct", "green", 4823,
                      "2026-07-08", "ok")
    doc = _health_load(tmp_path)
    assert doc["feeds"]["delivery_pct"]["status"] == "green"
    assert doc["feeds"]["delivery_pct"]["rows"] == 4823
    # append a second feed does not clobber the first
    _write_health_row(tmp_path, "iv_rank", "red", 0, None, "blocked")
    doc = _health_load(tmp_path)
    assert set(doc["feeds"]) == {"delivery_pct", "iv_rank"}
    assert doc["generated_at"] is not None


def test_shadow_recognizes_ma_50d_columns():
    """Shadow's build_core_input maps our MA_50D/MA_200D naming."""
    sys.path.insert(0, str(BASE))
    from nse_quant_engine_v4_shadow import build_core_input  # noqa: E402
    df = pd.DataFrame({
        "Symbol": ["A", "B"],
        "Return_21D": [0.05, -0.02],
        "Volatility_20D": [0.20, 0.35],
        "Price": [100.0, 200.0],
        "MA_50D": [95.0, 210.0],
        "MA_200D": [90.0, 220.0],
        "Benchmark_Return_21D": [0.01, 0.01],
    })
    out, warnings = build_core_input(df)
    # No warning about missing trend / bench columns
    joined = " ".join(warnings)
    assert "trend confirmation neutralized" not in joined, warnings
    assert "relative-strength confirmation neutralized" not in joined, warnings
    assert out["MA50"].iloc[0] == 95.0
    assert out["MA200"].iloc[1] == 220.0


def test_output_cols_include_trend_and_benchmark_columns():
    """nse_quant_engine.save_outputs must include MA/Benchmark_Return/Rel_Strength
    so shadow can consume trend + relative-strength inputs."""
    txt = (BASE / "nse_quant_engine.py").read_text()
    # scan the output_cols list literal
    for needed in ["MA_50D", "MA_200D", "Benchmark_Return_21D",
                   "Relative_Strength_21D", "Above_20DMA"]:
        assert f'"{needed}"' in txt, f"missing {needed} in nse_quant_engine.py output_cols"


def test_fundamentals_scaffold_when_yfinance_empty(monkeypatch, tmp_path):
    """When yfinance returns nothing, fetch_fundamentals must still emit a
    Symbol+Fundamental_Score scaffold and mark health AMBER."""
    from core import optional_data_fetchers as odf
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "latest_scores.csv").write_text("Symbol\nRELIANCE\nTCS\n")
    data_dir = tmp_path / "data"

    # Stub the yfinance wrapper to return empty
    monkeypatch.setattr(
        "core.fundamental_factor.fetch_fundamentals",
        lambda syms, sleep=0: pd.DataFrame(),
    )
    ok = odf.fetch_fundamentals(data_dir, tmp_path, cap=2)
    assert ok
    f = pd.read_csv(data_dir / "fundamentals_latest.csv")
    assert "Symbol" in f.columns and "Fundamental_Score" in f.columns
    assert set(f["Symbol"]) == {"RELIANCE", "TCS"}
    assert f["Fundamental_Score"].isna().all()
    doc = odf._health_load(data_dir)
    assert doc["feeds"]["fundamentals"]["status"] in ("amber", "red")
