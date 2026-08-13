"""
Cost-aware expected value (clean core v4.1 reviewed).

This estimates the historical expected return per holding day, but only after
validation is positive. Unlike the original v4 helper, this version supports
candidate-relevant filtering. Without filtering, EV can become one generic number
for all past signals, which is not useful for deciding today's candidates.

Recommended filters: ``Signal_Bucket``, ``Universe_Group``, ``Opportunity_Type``,
or other columns actually written by ``validation_builder.FORWARD_COLUMNS``. Note
the bucket column on forward-return history is ``Signal_Bucket`` (values such as
"Top Candidate" / "Candidate" / "Watch" / "Avoid") — there is no
``Score_Bucket`` column and no "Top Quintile" bucket in this engine.

The function is fail-safe AND fail-closed: EV returns NaN when validation is not
positive, when the sample is too thin, when the history schema is incompatible,
or when a requested filter column does not exist. It never publishes an
unfiltered number under a filtered label.
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


def _apply_filters(
    fwd: pd.DataFrame, filters: dict | None = None
) -> tuple[pd.DataFrame, str, list[str]]:
    """Filter forward-return history by exact-match columns.

    Example filters:
        {"Signal_Bucket": "Top Candidate", "Universe_Group": "Nifty50"}

    Returns ``(filtered, label, missing_columns)``.

    A requested filter column that does not exist is NEVER silently dropped: it
    is returned in ``missing_columns`` so the caller can refuse to publish an
    unfiltered statistic under a filtered label. Silently ignoring the filter
    used to turn "EV of the top bucket" into "EV of everything ever signalled",
    which is the sort of number that gets acted on.
    """
    if fwd is None or fwd.empty or not filters:
        return fwd, "unfiltered", []
    out = fwd.copy()
    parts: list[str] = []
    missing: list[str] = []
    for col, val in filters.items():
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        if col not in out.columns:
            missing.append(col)
            continue
        out = out[out[col].astype(str).str.upper().eq(str(val).upper())]
        parts.append(f"{col}={val}")
    label = "; ".join(parts) if parts else "unfiltered"
    if missing:
        label += f"; MISSING_FILTER_COLS={','.join(missing)}"
    return out, label, missing


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

    def _blank(filter_label: str, status: str, missing: list[str] | None = None,
               stats: dict | None = None) -> dict:
        stats = stats or {}
        return {
            "ev_per_trade": np.nan, "ev_per_day": np.nan,
            "p_win": stats.get("p_win", np.nan),
            "avg_win": stats.get("avg_win", np.nan),
            "avg_loss": stats.get("avg_loss", np.nan),
            "n_obs": stats.get("n", 0),
            "filter": filter_label,
            "missing_filter_columns": list(missing or []),
            "status": status,
        }

    if verdict != "validation positive":
        return _blank(
            "not_evaluated",
            f"EV unavailable — validation not positive ({validation_status.get('verdict','unknown')})",
        )

    if fwd_history is None or fwd_history.empty:
        return _blank("empty", "EV unavailable — no forward-return history")

    # Schema guard: _bucket_stats needs these two columns. Without them the
    # correct answer is "unavailable", not a KeyError from three frames down.
    required = [c for c in ("Horizon_Days", "Net_Forward_Return")
                if c not in fwd_history.columns]
    if required:
        return _blank(
            "schema_incompatible",
            "EV unavailable — forward-return history schema incompatible "
            f"(missing {', '.join(required)})",
        )

    fwd, label, missing_cols = _apply_filters(fwd_history, filters)

    # Fail closed: a filter that could not be applied must not be reported as
    # though it had been. Never compute an unfiltered statistic under a
    # filtered label.
    if missing_cols:
        return _blank(
            label,
            "EV unavailable — requested filter columns absent from "
            f"forward-return history ({', '.join(missing_cols)})",
            missing=missing_cols,
        )

    stats = _bucket_stats(fwd, horizon)
    if stats["n"] < min_obs:
        return _blank(
            label,
            f"EV unavailable — only {stats['n']} obs for filter [{label}] (<{min_obs})",
            stats=stats,
        )

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
        "missing_filter_columns": [],
        "status": "EV computed from validated filtered forward returns",
    }


# ────────────────────────────────────────────────────────────────────────────
# Step 12 — Top-5 EV & Kelly-Lite sizing cross-check.
# Uses aggregate backtest stats (Step 9) applied per-pick, tempered by the
# pick's own downside vol (Step 3). Report-only unless KELLY_OVERRIDE=1.
# ────────────────────────────────────────────────────────────────────────────

def _pick_backtest_row(backtest: pd.DataFrame) -> dict:
    if backtest is None or backtest.empty or "Variant" not in backtest.columns:
        return {}
    try:
        sub = backtest[backtest["Variant"].astype(str).str.contains("Top5", na=False)]
        if sub.empty:
            sub = backtest.head(1)
        r = sub.iloc[0].to_dict()
        return {k: (float(v) if isinstance(v, (int, float)) and not pd.isna(v) else v)
                for k, v in r.items()}
    except Exception:
        return {}


def top5_ev_report(top5: pd.DataFrame,
                   backtest: pd.DataFrame | None,
                   horizon_df: pd.DataFrame | None = None,
                   sizing: pd.DataFrame | None = None,
                   kelly_cap_of_weight: float = 0.25,
                   validation_verdict: str = "") -> pd.DataFrame:
    """Per-pick EV_% (per-trade) and Kelly_Fraction_Capped.

    Formula:
      p = Hit_Rate                        (from backtest_scorecard, Top5 variant)
      W = AvgWin_%/100, L = |AvgLoss_%/100|
      EV_%     = 100 * (p*W - (1-p)*L)
      Kelly    = max(0, (p*W - (1-p)*L) / max(L*W, 1e-9))    (edge/odds)
      Kelly_Cap = min(Kelly, kelly_cap_of_weight * Weight_%/100)

    Never raises. Missing inputs → NaN columns.
    """
    cols = ["Symbol", "EV_%", "P_Win", "AvgWin_%", "AvgLoss_%",
            "Downside_Vol_%", "Weight_%", "Kelly_Raw", "Kelly_Fraction_Capped",
            "EV_Sizing_Agree", "Validation_Verdict", "Decision_Use", "Basis"]
    validated = str(validation_verdict).strip().lower() == "validation positive"
    if top5 is None or top5.empty:
        return pd.DataFrame(columns=cols)

    b = _pick_backtest_row(backtest if backtest is not None else pd.DataFrame())
    p = b.get("Hit_Rate", np.nan)
    W = b.get("AvgWin_%", np.nan)
    L = b.get("AvgLoss_%", np.nan)
    try:
        p = float(p); W_f = float(W) / 100.0
        L_f = abs(float(L)) / 100.0 if pd.notna(L) else np.nan
        ev = (p * W_f - (1 - p) * L_f) * 100.0 if pd.notna(L_f) else np.nan
    except Exception:
        ev = np.nan; W_f = np.nan; L_f = np.nan; p = np.nan
    try:
        kelly_raw = max(0.0, (p * W_f - (1 - p) * L_f) / max(L_f * W_f, 1e-9)) \
            if all(pd.notna(x) for x in (p, W_f, L_f)) else np.nan
    except Exception:
        kelly_raw = np.nan

    hmap: dict[str, float] = {}
    if horizon_df is not None and not horizon_df.empty and "Symbol" in horizon_df.columns \
            and "Downside_Vol_%" in horizon_df.columns:
        for _, r in horizon_df.iterrows():
            try:
                hmap[str(r["Symbol"])] = float(r["Downside_Vol_%"])
            except Exception:
                pass

    wmap: dict[str, float] = {}
    if sizing is not None and not sizing.empty and "Symbol" in sizing.columns \
            and "Weight_%" in sizing.columns:
        for _, r in sizing.iterrows():
            try:
                wmap[str(r["Symbol"])] = float(r["Weight_%"])
            except Exception:
                pass

    rows = []
    for _, r in top5.iterrows():
        sym = str(r.get("Symbol", ""))
        if not sym:
            continue
        dv = hmap.get(sym, np.nan)
        w = wmap.get(sym, np.nan)
        cap = (kelly_cap_of_weight * (w / 100.0)) if pd.notna(w) else np.nan
        kc = min(kelly_raw, cap) if (pd.notna(kelly_raw) and pd.notna(cap)) else np.nan
        if not validated:
            # Historical style diagnostics only — no sizing instruction.
            kc = np.nan
        agree = ""
        if pd.notna(ev) and pd.notna(w):
            if ev > 0 and w > 0:
                agree = "Consistent"
            elif ev <= 0 and w > 0:
                agree = "Inconsistent — EV≤0 but sized"
            else:
                agree = "Neutral"
        if agree and not validated:
            agree += " (no approval implied)"
        rows.append({
            "Symbol": sym,
            "EV_%": round(ev, 3) if pd.notna(ev) else np.nan,
            "P_Win": round(p, 3) if pd.notna(p) else np.nan,
            "AvgWin_%": round(float(W), 3) if pd.notna(W) else np.nan,
            "AvgLoss_%": round(float(L), 3) if pd.notna(L) else np.nan,
            "Downside_Vol_%": round(dv, 3) if pd.notna(dv) else np.nan,
            "Weight_%": round(w, 3) if pd.notna(w) else np.nan,
            "Kelly_Raw": round(kelly_raw, 4) if pd.notna(kelly_raw) else np.nan,
            "Kelly_Fraction_Capped": round(kc, 4) if pd.notna(kc) else np.nan,
            "EV_Sizing_Agree": agree,
            "Validation_Verdict": str(validation_verdict or "Unknown"),
            "Decision_Use": ("Decision_Input" if validated
                             else "Not_For_Decisions_Watchlist_Only"),
            "Basis": ("VALIDATED_EV" if validated
                      else "STYLE_BACKTEST_DIAGNOSTIC"),
        })
    return pd.DataFrame(rows, columns=cols)
