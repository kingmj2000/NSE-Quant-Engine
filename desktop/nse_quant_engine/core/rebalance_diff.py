"""
Step 16 — Turnover / Rebalance Report vs Previous Top-5.

Compares today's top-5 against the last archived snapshot and emits
rebalance_diff.json with holds / exits / entries, estimated turnover and
net edge after round-trip cost.

On first run (no prior snapshot), reports 100% new entries and net edge = NaN.
Never raises.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd


def _diff(prev: pd.DataFrame, curr: pd.DataFrame) -> tuple[list, list, list]:
    p_syms = set(prev["Symbol"].astype(str)) if not prev.empty else set()
    c_syms = set(curr["Symbol"].astype(str)) if not curr.empty else set()
    holds = sorted(p_syms & c_syms)
    exits = sorted(p_syms - c_syms)
    entries = sorted(c_syms - p_syms)
    return holds, exits, entries


def _exit_reason(sym: str, prev_row: pd.Series, all_curr: pd.DataFrame,
                 sentiment: pd.DataFrame | None,
                 events: pd.DataFrame | None) -> str:
    reasons = []
    # sentiment veto
    if sentiment is not None and not sentiment.empty and "Symbol" in sentiment.columns:
        sub = sentiment[sentiment["Symbol"].astype(str) == sym]
        if not sub.empty:
            veto = sub.iloc[0].get("Veto_Flag") or sub.iloc[0].get("Sentiment_Veto")
            if str(veto).lower() in ("true", "1", "yes"):
                reasons.append("sentiment veto")
    # event risk
    if events is not None and not events.empty and "Symbol" in events.columns:
        sub = events[events["Symbol"].astype(str) == sym]
        if not sub.empty and str(sub.iloc[0].get("Event_Risk_Flag", "")) == "In_Window":
            reasons.append("earnings inside window")
    # score decay
    if not all_curr.empty and "Symbol" in all_curr.columns:
        sub = all_curr[all_curr["Symbol"].astype(str) == sym]
        if not sub.empty:
            prev_score = pd.to_numeric(prev_row.get("Confidence_Adjusted_Score", prev_row.get("Final_Score")), errors="coerce")
            curr_score = pd.to_numeric(sub.iloc[0].get("Confidence_Adjusted_Score", sub.iloc[0].get("Final_Score")), errors="coerce")
            if pd.notna(prev_score) and pd.notna(curr_score) and curr_score < prev_score - 3:
                reasons.append(f"score decay ({prev_score:.1f}→{curr_score:.1f})")
        else:
            reasons.append("dropped from universe")
    return "; ".join(reasons) if reasons else "outranked"


def build(curr_top5: pd.DataFrame,
          prev_snapshot: Path,
          all_curr: pd.DataFrame | None = None,
          sentiment: pd.DataFrame | None = None,
          events: pd.DataFrame | None = None,
          horizon_df: pd.DataFrame | None = None,
          round_trip_cost_pct: float = 0.35) -> dict:
    """Returns report dict. Also archives current top-5 snapshot."""
    out = {
        "as_of": pd.Timestamp.now().isoformat(timespec="seconds"),
        "prior_snapshot": None,
        "holds": [], "exits": [], "entries": [],
        "exit_reasons": {},
        "estimated_turnover_%": None,
        "estimated_round_trip_cost_%": round_trip_cost_pct,
        "expected_new_basket_return_%": None,
        "net_edge_after_cost_%": None,
        "recommendation": "Insufficient_History",
    }
    prev = pd.DataFrame()
    prev_snapshot = Path(prev_snapshot)
    if prev_snapshot.exists():
        try:
            prev = pd.read_csv(prev_snapshot)
            out["prior_snapshot"] = prev_snapshot.name
        except Exception:
            prev = pd.DataFrame()

    if curr_top5 is None or curr_top5.empty:
        return out
    curr = curr_top5.copy()
    curr["Symbol"] = curr["Symbol"].astype(str)

    if prev.empty:
        out["entries"] = curr["Symbol"].tolist()
        out["estimated_turnover_%"] = 100.0
        out["recommendation"] = "First_Run_Establish_Positions"
    else:
        holds, exits, entries = _diff(prev, curr)
        out["holds"] = holds
        out["exits"] = exits
        out["entries"] = entries
        n = max(len(curr), 1)
        out["estimated_turnover_%"] = round((len(entries) / n) * 100.0, 1)
        for s in exits:
            row = prev[prev["Symbol"].astype(str) == s].iloc[0]
            out["exit_reasons"][s] = _exit_reason(
                s, row, all_curr if all_curr is not None else pd.DataFrame(),
                sentiment, events)

        # Expected new-basket return uses horizon expected return of entries
        if horizon_df is not None and not horizon_df.empty and entries:
            hcol = "Exp_Ret_%" if "Exp_Ret_%" in horizon_df.columns else None
            if hcol:
                sub = horizon_df[horizon_df["Symbol"].astype(str).isin(entries)]
                if not sub.empty:
                    exp = float(pd.to_numeric(sub[hcol], errors="coerce").mean())
                    out["expected_new_basket_return_%"] = round(exp, 2)
                    # cost is charged on the turned-over portion
                    cost = round_trip_cost_pct * (out["estimated_turnover_%"] / 100.0)
                    out["net_edge_after_cost_%"] = round(exp - cost, 2)

        # Recommendation
        edge = out["net_edge_after_cost_%"]
        if out["estimated_turnover_%"] <= 20:
            out["recommendation"] = "Hold_Minor_Adjustment"
        elif edge is not None and edge > 0.5:
            out["recommendation"] = "Rotate_Edge_Positive"
        elif edge is not None and edge <= 0:
            out["recommendation"] = "Hold_Cost_Exceeds_Edge"
        else:
            out["recommendation"] = "Rotate_Manual_Review"

    # Archive current snapshot for next run
    try:
        prev_snapshot.parent.mkdir(parents=True, exist_ok=True)
        keep = ["Symbol", "Final_Score", "Confidence_Adjusted_Score",
                "Trade_Status", "Price"]
        curr[[c for c in keep if c in curr.columns]].to_csv(prev_snapshot, index=False)
    except Exception:
        pass

    return out
