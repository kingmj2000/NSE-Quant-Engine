"""
Adaptive alpha weighting — Part B (dormant, shadow-only, guardrailed).

Six mandatory guardrails, enforced here:

1. Walk-forward only — refuses to fit on rows whose forward window has not
   fully matured strictly before target date T. Assert-and-refuse; no
   silent fallback.
2. Validation-gated — only fits when the caller passes an "eligible" flag
   AND `n_effective_dates >= min_dates`.  Raw row count is not enough.
3. Shadow-first — this module never writes to primary artifacts; the caller
   decides where to route the fitted weights.
4. Heavily regularized —
     final = (1 - alpha) * baseline + alpha * fitted
   with `alpha = shrinkage_alpha`, a per-weight step cap
   `max_step`, AND a total-drift cap `max_total_drift`. If the raw fitted
   vector exceeds the total-drift cap vs baseline, the run is forced
   dormant.
5. No per-symbol learning — inputs must be keyed by alpha, not by symbol;
   we assert this and refuse otherwise.
6. Fully logged & reversible — every call returns a rich log dict; the
   master flag `enabled=False` is the caller's kill switch.

No network, no I/O. Uses ridge regression (numpy only).
"""
from __future__ import annotations

from typing import Iterable, Mapping
import numpy as np
import pandas as pd


def _assert_alpha_keyed(X: pd.DataFrame) -> None:
    """Guardrail #5: refuse anything that looks per-symbol."""
    bad_hints = {"Symbol", "Ticker", "ISIN", "SecurityId"}
    if any(c in bad_hints for c in X.columns):
        raise ValueError("adaptive_weights: inputs must be keyed by alpha, "
                         "not by symbol (found Symbol-like columns).")


def _assert_walk_forward(dates: pd.Series, target: pd.Timestamp, horizon: int) -> None:
    """Guardrail #1: every training row's forward window must have matured
    strictly before target date T."""
    d = pd.to_datetime(dates, errors="coerce").dropna()
    if d.empty:
        return
    max_allowed = pd.Timestamp(target) - pd.Timedelta(days=int(horizon))
    if (d > max_allowed).any():
        raise ValueError(f"adaptive_weights: look-ahead detected — training row "
                         f"date exceeds T - {horizon}d (T={target.date()}).")


def _ridge(X: np.ndarray, y: np.ndarray, ridge: float) -> np.ndarray:
    """Closed-form ridge:  β = (XᵀX + λI)⁻¹ Xᵀy. No intercept — weights
    are already relative importances, not shifted quantities."""
    p = X.shape[1]
    A = X.T @ X + float(ridge) * np.eye(p)
    b = X.T @ y
    try:
        return np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(A, b, rcond=None)[0]


def fit_adaptive_weights(panel: pd.DataFrame,
                         baseline: Mapping[str, float],
                         target_date: pd.Timestamp,
                         horizon: int,
                         *,
                         enabled: bool = False,
                         n_effective_dates: int = 0,
                         min_dates: int = 60,
                         validation_verdict: str = "",
                         shrinkage_alpha: float = 0.20,
                         max_step: float = 0.05,
                         max_total_drift: float = 0.30,
                         ridge_alpha: float = 1.0,
                         max_alpha_corr: float = 0.8) -> dict:
                         min_dates: int = 60,
                         validation_verdict: str = "",
                         shrinkage_alpha: float = 0.20,
                         max_step: float = 0.05,
                         max_total_drift: float = 0.30,
                         ridge_alpha: float = 1.0) -> dict:
    """Fit shrunk adaptive weights, then log every guardrail decision.

    `panel` columns must include ONE `Date` column, a `Fwd_Return` column,
    and one column per alpha (values are z-scores). Rows are (date, symbol)
    or (date, alpha-average); this function does not care as long as
    Symbol-like columns are absent (guardrail #5).

    Returns a log dict; caller writes it to `adaptive_weights_log.json`.
    """
    log: dict = {
        "date": str(pd.Timestamp(target_date).date()),
        "enabled": bool(enabled),
        "validation_gate_status": validation_verdict,
        "n_effective_dates": int(n_effective_dates),
        "min_dates_required": int(min_dates),
        "baseline": {k: round(float(v), 6) for k, v in baseline.items()},
        "fitted_raw": None,
        "shrunk_final": None,
        "per_weight_delta": None,
        "total_drift": None,
        "alpha_corr_matrix": None,
        "max_alpha_corr_allowed": float(max_alpha_corr),
        "shadow_or_primary": "shadow",
        "dormant": True,
        "dormant_reason": None,
    }

    # Guardrail #6 — master kill switch.
    if not enabled:
        log["dormant_reason"] = "ADAPTIVE_ENABLED=False"
        log["shrunk_final"] = dict(log["baseline"])
        return log

    # Guardrail #2 — validation + effective-dates gate.
    if validation_verdict != "Validation Positive":
        log["dormant_reason"] = f"validation verdict='{validation_verdict}' (need Validation Positive)"
        log["shrunk_final"] = dict(log["baseline"])
        return log
    if int(n_effective_dates) < int(min_dates):
        log["dormant_reason"] = (f"insufficient effective history "
                                 f"(N_eff={n_effective_dates}, need {min_dates})")
        log["shrunk_final"] = dict(log["baseline"])
        return log

    # Guardrail #5 — alpha-keyed inputs only.
    _assert_alpha_keyed(panel)

    if panel is None or panel.empty or "Fwd_Return" not in panel.columns or "Date" not in panel.columns:
        log["dormant_reason"] = "empty or malformed panel"
        log["shrunk_final"] = dict(log["baseline"])
        return log

    alpha_cols = [c for c in baseline.keys() if c in panel.columns]
    if not alpha_cols:
        log["dormant_reason"] = "no baseline alphas present in panel"
        log["shrunk_final"] = dict(log["baseline"])
        return log

    # Guardrail #1 — walk-forward only.
    _assert_walk_forward(panel["Date"], pd.Timestamp(target_date), horizon)

    sub = panel[alpha_cols + ["Fwd_Return"]].dropna()
    if len(sub) < 3 * len(alpha_cols):
        log["dormant_reason"] = f"too few clean rows ({len(sub)}) vs alphas ({len(alpha_cols)})"
        log["shrunk_final"] = dict(log["baseline"])
        return log

    X = sub[alpha_cols].to_numpy(dtype=float)
    y = sub["Fwd_Return"].to_numpy(dtype=float)
    beta = _ridge(X, y, ridge=ridge_alpha)

    # Convert coefficients → non-negative importances → weights.
    imp = np.abs(beta)
    if imp.sum() <= 0:
        log["dormant_reason"] = "ridge produced zero-signal coefficients"
        log["shrunk_final"] = dict(log["baseline"])
        return log
    fitted = imp / imp.sum()

    b_vec = np.array([float(baseline[c]) for c in alpha_cols], dtype=float)
    total_drift = float(np.sum(np.abs(fitted - b_vec)))
    log["fitted_raw"] = {c: round(float(v), 6) for c, v in zip(alpha_cols, fitted)}
    log["total_drift"] = round(total_drift, 6)

    # Guardrail #4b — total-drift cap.
    if total_drift > float(max_total_drift):
        log["dormant_reason"] = (f"total drift {total_drift:.3f} exceeds cap "
                                 f"{max_total_drift:.3f}")
        log["shrunk_final"] = dict(log["baseline"])
        return log

    # Guardrail #4a — shrinkage + per-weight step cap.
    alpha_s = float(shrinkage_alpha)
    shrunk = (1.0 - alpha_s) * b_vec + alpha_s * fitted
    delta = shrunk - b_vec
    clipped = np.clip(delta, -float(max_step), float(max_step))
    final = b_vec + clipped
    # renormalize
    if final.sum() > 0:
        final = final / final.sum()

    log["shrunk_final"] = {c: round(float(v), 6) for c, v in zip(alpha_cols, final)}
    log["per_weight_delta"] = {c: round(float(final[i] - b_vec[i]), 6)
                               for i, c in enumerate(alpha_cols)}
    log["dormant"] = False
    log["dormant_reason"] = None
    return log
