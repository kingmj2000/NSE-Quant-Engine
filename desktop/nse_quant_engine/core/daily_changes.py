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
from datetime import datetime, timezone
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


CLEAN_RISK_TOKENS = {"", "nan", "none", "null", "clean", "no risk", "no risk flag", "-"}


def _is_clean_flag(raw) -> bool:
    """True when a Risk_Flag value means 'no active risk'."""
    if raw is None:
        return True
    if isinstance(raw, float) and pd.isna(raw):
        return True
    return str(raw).strip().lower() in CLEAN_RISK_TOKENS


def _risk_flag_set(df: pd.DataFrame) -> dict[str, str]:
    """{symbol: active_risk_flag_text} for rows carrying a non-clean flag.

    Blank / missing / NaN / "Clean" all mean *no active risk* and are omitted.
    """
    if df is None or df.empty or "Symbol" not in df.columns or "Risk_Flag" not in df.columns:
        return {}
    out: dict[str, str] = {}
    for r in df.itertuples(index=False):
        raw = getattr(r, "Risk_Flag", None)
        if _is_clean_flag(raw):
            continue
        out[str(getattr(r, "Symbol", ""))] = str(raw).strip()
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

    # Current official score date. The scoring run appends the current snapshot
    # to score_history.csv *before* this builder runs, so the previous snapshot
    # must be the latest distinct history date STRICTLY EARLIER than the
    # current official date — never the current run compared against itself.
    curr_date = pd.NaT
    if not curr.empty and "Date" in curr.columns:
        curr_date = pd.to_datetime(curr["Date"], errors="coerce").dropna().max()

    prev = pd.DataFrame()
    prev_date = pd.NaT
    if not hist.empty and "Date" in hist.columns:
        try:
            hist = hist.copy()
            hist["Date"] = pd.to_datetime(hist["Date"], errors="coerce")
            dates = sorted(hist["Date"].dropna().unique())
            if pd.isna(curr_date) and dates:
                # No date on latest_scores.csv — treat the newest history date
                # as the current run.
                curr_date = dates[-1]
            earlier = [d for d in dates if pd.notna(curr_date) and d < curr_date]
            if earlier:
                prev_date = earlier[-1]
                prev = hist[hist["Date"] == prev_date].copy()
        except Exception:
            prev = pd.DataFrame()
            prev_date = pd.NaT


    # Official Top-5 / Top-20 diffs — only meaningful when we have a prior
    # snapshot to diff against. On a first-ever run every current name would
    # otherwise be flagged as an "entrant".
    has_prev = not prev.empty
    curr_top5, prev_top5 = set(_top_n(curr, 5)), set(_top_n(prev, 5))
    curr_top20, prev_top20 = set(_top_n(curr, 20)), set(_top_n(prev, 20))

    top5_entries  = sorted(curr_top5  - prev_top5)  if has_prev else []
    top5_exits    = sorted(prev_top5  - curr_top5)  if has_prev else []
    top20_entries = sorted(curr_top20 - prev_top20) if has_prev else []
    top20_exits   = sorted(prev_top20 - curr_top20) if has_prev else []

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

    # Risk changes. Blank / missing / NaN / "Clean" all mean no active risk.
    #   new_risk_flags     — current flag is non-clean AND differs from previous
    #                        (covers clean→risk and risk→different-risk).
    #   cleared_risk_flags — previous flag was non-clean and current is clean.
    curr_flags = _risk_flag_set(curr)
    prev_flags = _risk_flag_set(prev)
    new_flags = [
        {"Symbol": s, "flag": curr_flags[s], "previous_flag": prev_flags.get(s)}
        for s in sorted(curr_flags)
        if curr_flags[s] != prev_flags.get(s)
    ]
    cleared_flags = [
        {"Symbol": s, "previous_flag": prev_flags[s], "flag": None}
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
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ranking_column": RANKING_COLUMN,
        "current_score_date": None if pd.isna(curr_date) else str(pd.Timestamp(curr_date).date()),
        "previous_score_date": None if pd.isna(prev_date) else str(pd.Timestamp(prev_date).date()),
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
