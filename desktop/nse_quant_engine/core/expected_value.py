"""
Cost-aware expected value (clean core v4.1 reviewed).

This estimates the historical expected return per holding day, but only after
validation is positive. Unlike the original v4 helper, this version supports
candidate-relevant filtering. Without filtering, EV can become one generic number
for all past signals, which is not useful for deciding today's candidates.

Recommended filters: score bucket/decile, Universe_Group, Opportunity_Type, or
other columns present in forward_return_history. The function remains fail-safe:
if validation is not positive or observations are too thin, EV returns NaN.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from . import config as C


def _bucket_stats(fwd: pd.DataFrame, horizon: int) -> dict:
    h = fwd[fwd["Horizon_Days"] == horizon]
    net = pd.to_numeric(h.get("Net_Forward_Return"), errors="coerce").dropna()
    if len(net) == 0:
        return {"n": 0, "p_win": np.nan, "avg_win": np.nan, "avg_loss": np.nan}
    wins = net[net > 0]
    losses = net[net <= 0]
    return {
        "n": int(len(net)),
        "p_win": float(len(wins) / len(net)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(abs(losses.mean())) if len(losses) else 0.0,
    }


def _apply_filters(fwd: pd.DataFrame, filters: dict | None = None) -> tuple[pd.DataFrame, str]:
    """Filter forward-return history by exact-match columns.

    Example filters:
        {"Score_Bucket": "Top Quintile", "Universe_Group": "Nifty50"}

    Missing columns are ignored but recorded in the label, so integration does
    not crash when old history files lack a new metadata field.
    """
    if fwd is None or fwd.empty or not filters:
        return fwd, "unfiltered"
    out = fwd.copy()
    parts = []
    ignored = []
    for col, val in filters.items():
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        if col not in out.columns:
            ignored.append(col)
            continue
        out = out[out[col].astype(str).str.upper().eq(str(val).upper())]
        parts.append(f"{col}={val}")
    label = "; ".join(parts) if parts else "unfiltered"
    if ignored:
        label += f"; ignored_missing_cols={','.join(ignored)}"
    return out, label


def expected_value_per_day(
    fwd_history: pd.DataFrame,
    validation_status: dict,
    horizon: int = 10,
    hold_days: int | None = None,
    filters: dict | None = None,
    min_obs: int | None = None,
) -> dict:
    """Return EV stats for a relevant slice of forward-return history.

    Returns:
        {ev_per_trade, ev_per_day, p_win, avg_win, avg_loss, n_obs, status, filter}

    If validation has not cleared, or the filtered sample is too thin, EV is NaN.
    """
    hold_days = hold_days or int(np.median([C.HOLD_DAYS_MIN, C.HOLD_DAYS_MAX]))
    min_obs = min_obs or C.EV_MIN_OBS
    verdict = str(validation_status.get("verdict", "")).strip().lower()

    if verdict != "validation positive":
        return {
            "ev_per_trade": np.nan, "ev_per_day": np.nan,
            "p_win": np.nan, "avg_win": np.nan, "avg_loss": np.nan,
            "n_obs": 0, "filter": "not_evaluated",
            "status": f"EV unavailable — validation not positive ({validation_status.get('verdict','unknown')})",
        }

    if fwd_history is None or fwd_history.empty:
        return {"ev_per_trade": np.nan, "ev_per_day": np.nan, "p_win": np.nan,
                "avg_win": np.nan, "avg_loss": np.nan, "n_obs": 0,
                "filter": "empty", "status": "EV unavailable — no forward-return history"}

    fwd, label = _apply_filters(fwd_history, filters)
    stats = _bucket_stats(fwd, horizon)
    if stats["n"] < min_obs:
        return {"ev_per_trade": np.nan, "ev_per_day": np.nan,
                "p_win": stats["p_win"], "avg_win": stats["avg_win"],
                "avg_loss": stats["avg_loss"], "n_obs": stats["n"],
                "filter": label,
                "status": f"EV unavailable — only {stats['n']} obs for filter [{label}] (<{min_obs})"}

    ev_trade = stats["p_win"] * stats["avg_win"] - (1 - stats["p_win"]) * stats["avg_loss"]
    ev_day = ev_trade / max(hold_days, 1)
    return {
        "ev_per_trade": round(ev_trade, 5),
        "ev_per_day": round(ev_day, 6),
        "p_win": round(stats["p_win"], 4),
        "avg_win": round(stats["avg_win"], 5),
        "avg_loss": round(stats["avg_loss"], 5),
        "n_obs": stats["n"],
        "filter": label,
        "status": "EV computed from validated filtered forward returns",
    }
