"""Post-ranking builder for `output/daily_changes.json`.

Read-only over official artifacts. Consumes:
    - output/latest_scores.csv                (current official ranking)
    - output/score_history.csv                (previous ranking snapshot)
    - output/macro_context.json               (regime, when available)

Emits a single structured JSON so downstream UI never has to invent shapes
from `rebalance_diff.json` (which only diffs the Top-5 basket) or from
free-form CSV comparisons.

Ordering authority = Confidence_Adjusted_Score (Symbol asc tie-break),
enforced via core.candidate_selection.canonical_order. Never introduces a
second ranking method.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .candidate_selection import canonical_order, is_eligible

SCHEMA_VERSION = "nse_daily_changes_v1"
RANKING_COLUMN = "Confidence_Adjusted_Score"


# ─── helpers ────────────────────────────────────────────────────────────────

def _safe_csv(p: Path) -> pd.DataFrame:
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _safe_json(p: Path) -> dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _top_n(df: pd.DataFrame, n: int) -> list[str]:
    if df is None or df.empty:
        return []
    ordered = canonical_order(df, eligible_only=True)
    return [str(s) for s in ordered["Symbol"].head(n).tolist()]


def _risk_flag_set(df: pd.DataFrame) -> dict[str, str]:
    """{symbol: risk_flag_text} for rows where the flag is non-empty."""
    if df is None or df.empty or "Symbol" not in df.columns or "Risk_Flag" not in df.columns:
        return {}
    out: dict[str, str] = {}
    for r in df.itertuples(index=False):
        flag = str(getattr(r, "Risk_Flag", "") or "").strip()
        if flag:
            out[str(getattr(r, "Symbol", ""))] = flag
    return out


def _rank_map(df: pd.DataFrame) -> dict[str, int]:
    """Symbol → 1-based canonical rank (eligible only)."""
    if df is None or df.empty:
        return {}
    ordered = canonical_order(df, eligible_only=True).reset_index(drop=True)
    return {str(s): int(i + 1) for i, s in enumerate(ordered["Symbol"].tolist())}


# ─── main builder ───────────────────────────────────────────────────────────

def build_daily_changes(base_dir: str | Path, out_dir: str | Path | None = None,
                        write: bool = True) -> dict:
    base = Path(base_dir)
    out = Path(out_dir) if out_dir else (base / "output")

    curr = _safe_csv(out / "latest_scores.csv")
    hist = _safe_csv(out / "score_history.csv")
    macro = _safe_json(out / "macro_context.json")

    prev = pd.DataFrame()
    if not hist.empty and "Date" in hist.columns:
        try:
            hist["Date"] = pd.to_datetime(hist["Date"], errors="coerce")
            latest = hist["Date"].dropna().max()
            if pd.notna(latest):
                prev = hist[hist["Date"] == latest].copy()
        except Exception:
            prev = pd.DataFrame()

    # Official Top-5 / Top-20 diffs.
    curr_top5, prev_top5 = set(_top_n(curr, 5)), set(_top_n(prev, 5))
    curr_top20, prev_top20 = set(_top_n(curr, 20)), set(_top_n(prev, 20))

    top5_entries = sorted(curr_top5 - prev_top5)
    top5_exits = sorted(prev_top5 - curr_top5)
    top20_entries = sorted(curr_top20 - prev_top20)
    top20_exits = sorted(prev_top20 - curr_top20)

    # Rank movers (join current vs previous canonical ranks).
    curr_r = _rank_map(curr)
    prev_r = _rank_map(prev)
    movers = []
    for sym, r_now in curr_r.items():
        r_prev = prev_r.get(sym)
        if r_prev is None:
            continue
        movers.append({
            "Symbol": sym,
            "previous_rank": int(r_prev),
            "current_rank": int(r_now),
            "rank_change": int(r_prev - r_now),   # +ve = improvement
        })
    movers_df = pd.DataFrame(movers)
    if not movers_df.empty:
        gainers = movers_df.sort_values(
            ["rank_change", "current_rank", "Symbol"],
            ascending=[False, True, True]).head(5).to_dict(orient="records")
        losers = movers_df.sort_values(
            ["rank_change", "current_rank", "Symbol"],
            ascending=[True, True, True]).head(5).to_dict(orient="records")
    else:
        gainers, losers = [], []

    # Risk flag additions / clearances.
    curr_flags = _risk_flag_set(curr)
    prev_flags = _risk_flag_set(prev)
    new_flags = [
        {"Symbol": s, "flag": curr_flags[s]}
        for s in sorted(set(curr_flags) - set(prev_flags))
    ]
    cleared_flags = [
        {"Symbol": s, "previous_flag": prev_flags[s]}
        for s in sorted(set(prev_flags) - set(curr_flags))
    ]

    # Regime change (best-effort — macro_context is optional).
    regime_now = str(macro.get("regime") or "") if isinstance(macro, dict) else ""
    regime_prev = str(macro.get("previous_regime") or "") if isinstance(macro, dict) else ""
    regime_change = None
    if regime_now and regime_prev and regime_now != regime_prev:
        regime_change = {"from": regime_prev, "to": regime_now}

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "ranking_column": RANKING_COLUMN,
        "previous_snapshot_available": not prev.empty,
        "top5_entries": top5_entries,
        "top5_exits": top5_exits,
        "top20_entries": top20_entries,
        "top20_exits": top20_exits,
        "largest_rank_gainers": gainers,
        "largest_rank_losers": losers,
        "new_risk_flags": new_flags,
        "cleared_risk_flags": cleared_flags,
        "regime_change": regime_change,
    }

    if write:
        out.mkdir(parents=True, exist_ok=True)
        (out / "daily_changes.json").write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent
    p = build_daily_changes(base)
    print(json.dumps({k: v for k, v in p.items()
                      if k != "candidate_coverage"}, indent=2, default=str))
