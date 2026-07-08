"""
Alpha-Zoo evaluator (Step 5, Vibe-Trading-inspired).

Given the alpha panel produced by `core/alpha_zoo.compute_alpha_zoo()` and
the historical prices frame, compute per-(alpha × horizon):
  * Spearman IC (rank correlation of the alpha value at date t with the
    forward H-day return realised at t+H).
  * IC t-stat  ≈ mean(IC) / (std(IC) / sqrt(k)) over walk-forward folds.
  * hit rate   = share of dates where sign(IC) matches the sign of aggregate mean IC.

`promote_alphas` returns the "survivor" list — alphas that clear IC and t-stat
thresholds. Survivors and the full IC table are written to disk by the caller
for the dashboard tile.

Pure functions — no I/O. Time-series ranked to be robust to non-stationary
alpha scales. Guarded against short histories (returns empty frame instead of
raising).
"""
from __future__ import annotations

from typing import Iterable
import numpy as np
import pandas as pd

from . import alpha_zoo


DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 21)
DEFAULT_EVAL_DAYS: int = 250
DEFAULT_FOLDS: int = 4


def _wide_closes(prices_long: pd.DataFrame) -> pd.DataFrame:
    if prices_long is None or prices_long.empty:
        return pd.DataFrame()
    df = prices_long.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Symbol", "Close"])
    return df.pivot_table(index="Date", columns="Symbol",
                          values="Close", aggfunc="last").sort_index()


def _fwd_returns(wide: pd.DataFrame, h: int) -> pd.DataFrame:
    """Forward H-day return per symbol per date.

    Using `.shift(-h)` here is intentional and safe: this is a *research-time*
    evaluation over strictly historical data, aligned to the alpha date on the
    left. The dashboard/pipeline never uses the shifted frame as a live signal.
    """
    if wide.empty:
        return wide
    return wide.shift(-h) / wide - 1.0


def _alpha_panel_per_date(prices_long: pd.DataFrame,
                          eval_dates: Iterable[pd.Timestamp],
                          alphas: list[str]) -> dict[pd.Timestamp, pd.DataFrame]:
    """For each evaluation date, run alpha_zoo on the historical slice ending
    at that date and return the panel. Expensive but bounded by
    len(eval_dates) × universe."""
    panels: dict[pd.Timestamp, pd.DataFrame] = {}
    df = prices_long.copy()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    for d in eval_dates:
        slice_ = df[df["Date"] <= d]
        if slice_.empty:
            continue
        try:
            z = alpha_zoo.compute_alpha_zoo(slice_)
        except Exception:
            continue
        keep = ["Symbol"] + [a for a in alphas if a in z.columns]
        panels[d] = z[keep].set_index("Symbol")
    return panels


def _spearman_ic(a: pd.Series, r: pd.Series) -> float:
    both = pd.concat([a, r], axis=1, join="inner").dropna()
    if len(both) < 8:
        return np.nan
    return float(both.iloc[:, 0].rank().corr(both.iloc[:, 1].rank()))


def evaluate_alphas(prices_long: pd.DataFrame,
                    horizons: Iterable[int] = DEFAULT_HORIZONS,
                    eval_days: int = DEFAULT_EVAL_DAYS,
                    folds: int = DEFAULT_FOLDS,
                    max_dates: int = 20) -> pd.DataFrame:
    """Return per-(alpha × horizon) IC statistics across walk-forward folds.

    `max_dates` bounds the compute cost: we sample up to that many evaluation
    dates evenly across the eval window (default 20 dates × 15 alphas × 3
    horizons = 900 IC points — fast on pandas).
    """
    cols = ["alpha", "horizon", "n_dates", "mean_IC", "std_IC",
            "t_stat", "hit_rate"]
    if prices_long is None or prices_long.empty:
        return pd.DataFrame(columns=cols)

    wide = _wide_closes(prices_long)
    if wide.empty or wide.shape[0] < max(horizons) + 20:
        return pd.DataFrame(columns=cols)

    dates = wide.index.sort_values()
    tail = dates[-eval_days:] if len(dates) > eval_days else dates
    if len(tail) < 30:
        return pd.DataFrame(columns=cols)

    step = max(1, len(tail) // max_dates)
    eval_dates = list(tail[::step])[-max_dates:]

    alpha_names = list(alpha_zoo.ALPHAS.keys())
    panels = _alpha_panel_per_date(prices_long, eval_dates, alpha_names)
    if not panels:
        return pd.DataFrame(columns=cols)

    fwd = {h: _fwd_returns(wide, h) for h in horizons}
    rows = []
    for a in alpha_names:
        for h in horizons:
            ics = []
            for d, panel in panels.items():
                if a not in panel.columns or d not in fwd[h].index:
                    continue
                r = fwd[h].loc[d]
                ic = _spearman_ic(panel[a], r)
                if pd.notna(ic):
                    ics.append(ic)
            if len(ics) < max(3, folds):
                rows.append({"alpha": a, "horizon": h, "n_dates": len(ics),
                             "mean_IC": np.nan, "std_IC": np.nan,
                             "t_stat": np.nan, "hit_rate": np.nan})
                continue
            arr = np.array(ics, dtype=float)
            m, sd = float(arr.mean()), float(arr.std(ddof=1))
            t = m / (sd / np.sqrt(len(arr))) if sd > 0 else np.nan
            hit = float((np.sign(arr) == np.sign(m)).mean()) if m != 0 else 0.5
            rows.append({"alpha": a, "horizon": h, "n_dates": len(arr),
                         "mean_IC": round(m, 4), "std_IC": round(sd, 4),
                         "t_stat": round(t, 3) if pd.notna(t) else np.nan,
                         "hit_rate": round(hit, 3)})
    return pd.DataFrame(rows, columns=cols).sort_values(
        ["horizon", "mean_IC"], ascending=[True, False]).reset_index(drop=True)


def promote_alphas(eval_df: pd.DataFrame,
                   min_ic: float = 0.03,
                   min_tstat: float = 2.0) -> list[dict]:
    """Return survivors as [{alpha, horizon, mean_IC, t_stat, hit_rate}]."""
    if eval_df is None or eval_df.empty:
        return []
    df = eval_df.copy()
    df["abs_ic"] = df["mean_IC"].abs()
    mask = (df["abs_ic"] >= min_ic) & (df["t_stat"].abs() >= min_tstat)
    keep = df[mask].sort_values(["abs_ic", "t_stat"], ascending=False)
    out = []
    seen: set[str] = set()
    for _, r in keep.iterrows():
        a = str(r["alpha"])
        if a in seen:
            continue
        seen.add(a)
        out.append({
            "alpha": a,
            "horizon": int(r["horizon"]),
            "mean_IC": float(r["mean_IC"]),
            "t_stat": float(r["t_stat"]) if pd.notna(r["t_stat"]) else None,
            "hit_rate": float(r["hit_rate"]) if pd.notna(r["hit_rate"]) else None,
        })
    return out
