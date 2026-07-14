"""Tests for issue 1 (adaptive panel wiring) and issue 3 (Top-5 alignment)."""
from __future__ import annotations

import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import adaptive_weights as aw  # noqa: E402
from nse_quant_engine_v4_shadow import build_adaptive_panel  # noqa: E402
from dashboard_html_builder import _assert_top5_alignment  # noqa: E402


BASELINE = {"momentum": 0.5, "trend": 0.3, "safety": 0.2}


def _write_panel_files(tmp_path: Path, n_dates: int = 80, rng_seed: int = 7):
    rng = np.random.default_rng(rng_seed)
    dates = pd.bdate_range("2024-01-01", periods=n_dates).strftime("%Y-%m-%d")
    symbols = [f"S{i}" for i in range(6)]
    rows_alpha = []
    rows_fwd = []
    for d in dates:
        for s in symbols:
            m = float(rng.standard_normal())
            t = float(rng.standard_normal())
            sf = float(rng.standard_normal())
            rows_alpha.append({"Date": d, "Symbol": s, "momentum": m, "trend": t, "safety": sf})
            # forward return correlated with momentum
            rows_fwd.append({"Date": d, "Symbol": s, "Fwd_Return": 0.4 * m + 0.1 * float(rng.standard_normal())})
    ap = tmp_path / "alpha_score_history.csv"
    fp = tmp_path / "forward_return_history.csv"
    pd.DataFrame(rows_alpha).to_csv(ap, index=False)
    pd.DataFrame(rows_fwd).to_csv(fp, index=False)
    return ap, fp


def test_panel_has_alpha_columns_and_no_symbol(tmp_path):
    ap, fp = _write_panel_files(tmp_path, n_dates=10)
    panel = build_adaptive_panel(ap, fp, list(BASELINE.keys()))
    assert not panel.empty
    for k in BASELINE:
        assert k in panel.columns, f"missing alpha column {k}"
    assert "Fwd_Return" in panel.columns
    assert "Symbol" not in panel.columns  # guardrail #5


def test_fit_produces_non_baseline_when_gated_open(tmp_path):
    ap, fp = _write_panel_files(tmp_path, n_dates=200)
    panel = build_adaptive_panel(ap, fp, list(BASELINE.keys()))
    log = aw.fit_adaptive_weights(
        panel=panel,
        baseline=BASELINE,
        target_date=pd.Timestamp("2030-01-01"),  # far future so walk-forward passes
        horizon=10,
        enabled=True,
        n_effective_dates=120,
        min_dates=60,
        validation_verdict="Validation Positive",
    )
    assert log["dormant"] is False, f"expected non-dormant, got: {log.get('dormant_reason')}"
    assert log["shrunk_final"] != log["baseline"]
    assert log["alpha_corr_matrix"] is not None


def test_collinearity_forces_dormant(tmp_path):
    """Feed a panel where trend == momentum → correlation ~1.0 → dormant."""
    rng = np.random.default_rng(3)
    dates = pd.bdate_range("2024-01-01", periods=150).strftime("%Y-%m-%d")
    rows = []
    for d in dates:
        for s in ("A", "B", "C", "D", "E"):
            m = float(rng.standard_normal())
            rows.append({"Date": d, "momentum": m, "trend": m,  # perfectly collinear
                         "safety": float(rng.standard_normal()),
                         "Fwd_Return": 0.3 * m})
    panel = pd.DataFrame(rows)
    log = aw.fit_adaptive_weights(
        panel=panel, baseline=BASELINE,
        target_date=pd.Timestamp("2030-01-01"), horizon=10,
        enabled=True, n_effective_dates=200, min_dates=60,
        validation_verdict="Validation Positive",
        max_alpha_corr=0.8,
    )
    assert log["dormant"] is True
    assert "collinearity" in (log["dormant_reason"] or "")
    assert log["alpha_corr_matrix"] is not None


# ── Issue 3: Top-5 alignment ────────────────────────────────────────────────

def _make_tp():
    return pd.DataFrame([
        {"Symbol": "A.NS", "Confidence_Adjusted_Score": 90, "Final_Score": 88},
        {"Symbol": "B.NS", "Confidence_Adjusted_Score": 85, "Final_Score": 92},
        {"Symbol": "C.NS", "Confidence_Adjusted_Score": 80, "Final_Score": 70},
        {"Symbol": "D.NS", "Confidence_Adjusted_Score": 78, "Final_Score": 82},
        {"Symbol": "E.NS", "Confidence_Adjusted_Score": 70, "Final_Score": 65},
        {"Symbol": "F.NS", "Confidence_Adjusted_Score": 60, "Final_Score": 95},
    ])


def test_top5_alignment_ok_when_same_sort():
    tp = _make_tp()
    # cards sorted by CAS (matches trade plan)
    cards = [{"sym": s} for s in ["A.NS", "B.NS", "C.NS", "D.NS", "E.NS"]]
    r = _assert_top5_alignment(cards, tp, "Confidence_Adjusted_Score", "Final_Score")
    assert r["ok"] is True


def test_top5_alignment_mismatch_when_different_sort():
    tp = _make_tp()
    # cards sorted by Final_Score → different order
    cards = [{"sym": s} for s in ["F.NS", "B.NS", "A.NS", "D.NS", "C.NS"]]
    r = _assert_top5_alignment(cards, tp, "Confidence_Adjusted_Score", "Final_Score")
    assert r["ok"] is False
    assert r["reason"]
    assert set(r["dash"]) != set(r["plan"][:5]) or r["dash"] != r["plan"]
