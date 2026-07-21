"""Centralized read-only schema adapters for the desktop UI.

All UI widgets MUST go through this module for every JSON/CSV produced by the
pipeline. No Qt is imported here so the readers can be unit-tested headlessly.

Each reader:
    - handles missing / unreadable files by returning an explicit empty shape,
    - normalizes known-current field names,
    - never writes back to the pipeline artifacts,
    - never fabricates values.

Also exposes:
    - COLUMN_ALIASES:  one shared mapping of UI-friendly names → production
      column names in latest_scores.csv (RSI_14, Volatility_20D,
      Current_Drawdown_60D, …).
    - pick_column():   resolve a UI-friendly name against an actual DataFrame.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


# ─── Shared column alias table ──────────────────────────────────────────────
# Ordered from most-preferred → most-tolerant fallback.
COLUMN_ALIASES: dict[str, list[str]] = {
    "RSI":         ["RSI_14", "RSI"],
    "Volatility":  ["Volatility_20D", "Volatility_60D", "Volatility", "Vol"],
    "Drawdown":    ["Current_Drawdown_60D", "Max_Drawdown_60D", "Drawdown", "Max_Drawdown_%"],
    "Confidence":  ["Confidence_Score", "Confidence"],
    "News_Count":  ["News_Count", "News_Recent_Count"],
    "Event":       ["Event", "Event_Risk_Flag"],
    "Score":       ["Confidence_Adjusted_Score"],
    "Raw_Score":   ["Final_Score"],
}


def pick_column(df: pd.DataFrame, name: str) -> str | None:
    """Return the first alias present in df.columns, or None.

    Note: a DataFrame with columns but zero rows is still valid for column
    resolution — we deliberately do NOT bail out on df.empty.
    """
    if df is None:
        return None
    cols = list(df.columns)
    if name in cols:
        return name
    for c in COLUMN_ALIASES.get(name, []):
        if c in cols:
            return c
    return None


# ─── Low-level safe readers ────────────────────────────────────────────────

def read_json(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8").replace(": NaN", ": null"))
    except Exception:
        return {}


def read_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


# ─── Versioned schema adapters ──────────────────────────────────────────────

def read_validation_status(out_dir: str | Path) -> dict:
    """`output/validation_status.json` — normalize to a stable shape.

    Returns keys: verdict, evidence_grade, horizon_days, ranking_column,
    ranking_schema_version, stats (dict), is_valid_positive (bool),
    schema (str), empty (bool).
    """
    data = read_json(Path(out_dir) / "validation_status.json")
    empty = not data
    verdict = str(data.get("verdict") or "Insufficient History")
    return {
        "verdict": verdict,
        "evidence_grade": str(data.get("evidence_grade") or "Insufficient Evidence"),
        "horizon_days": data.get("horizon_days"),
        "ranking_column": data.get("ranking_column") or "Confidence_Adjusted_Score",
        "ranking_schema_version": int(data.get("ranking_schema_version") or 0),
        "stats": data.get("stats") or {},
        "is_valid_positive": verdict == "Validation Positive",
        "schema": data.get("schema") or "nse_validation_status_v1",
        "empty": empty,
    }


def read_rebalance_diff(out_dir: str | Path) -> dict:
    """`output/rebalance_diff.json` — flat Top-5 diff produced by
    `core.rebalance_diff`. Fields: holds, exits, entries, exit_reasons,
    estimated_turnover_%, net_edge_after_cost_%, recommendation, as_of.
    """
    data = read_json(Path(out_dir) / "rebalance_diff.json")
    return {
        "as_of": data.get("as_of"),
        "holds": list(data.get("holds") or []),
        "exits": list(data.get("exits") or []),
        "entries": list(data.get("entries") or []),
        "exit_reasons": dict(data.get("exit_reasons") or {}),
        "estimated_turnover_pct": data.get("estimated_turnover_%"),
        "net_edge_after_cost_pct": data.get("net_edge_after_cost_%"),
        "recommendation": data.get("recommendation"),
        "empty": not data,
    }


def read_daily_changes(out_dir: str | Path) -> dict:
    """`output/daily_changes.json` — structured post-ranking daily diff.

    Fields: schema_version, generated_at, ranking_column, top5_entries,
    top5_exits, top20_entries, top20_exits, largest_rank_gainers,
    largest_rank_losers, new_risk_flags, cleared_risk_flags, regime_change.
    """
    data = read_json(Path(out_dir) / "daily_changes.json")
    return {
        "schema_version": data.get("schema_version"),
        "generated_at": data.get("generated_at"),
        "ranking_column": data.get("ranking_column") or "Confidence_Adjusted_Score",
        "top5_entries": list(data.get("top5_entries") or []),
        "top5_exits": list(data.get("top5_exits") or []),
        "top20_entries": list(data.get("top20_entries") or []),
        "top20_exits": list(data.get("top20_exits") or []),
        "largest_rank_gainers": list(data.get("largest_rank_gainers") or []),
        "largest_rank_losers": list(data.get("largest_rank_losers") or []),
        "new_risk_flags": list(data.get("new_risk_flags") or []),
        "cleared_risk_flags": list(data.get("cleared_risk_flags") or []),
        "regime_change": data.get("regime_change"),
        "empty": not data,
    }


def read_data_health(base_dir: str | Path) -> dict:
    """`data/data_health.json` — schema:
        {generated_at, feeds: {feed_name: {status, rows, last_date, note}}}.

    Returns: {generated_at, feeds (dict), reds (list[str]), ambers (list[str]),
              empty (bool)}.
    """
    data = read_json(Path(base_dir) / "data" / "data_health.json")
    feeds = data.get("feeds") or {}
    if not isinstance(feeds, dict):
        feeds = {}
    reds, ambers = [], []
    for name, meta in feeds.items():
        if not isinstance(meta, dict):
            continue
        s = str(meta.get("status", "")).lower()
        if s == "red":
            reds.append(str(name))
        elif s == "amber":
            ambers.append(str(name))
    return {
        "generated_at": data.get("generated_at"),
        "feeds": feeds,
        "reds": reds,
        "ambers": ambers,
        "empty": not data,
    }


def read_shadow_summary(out_dir: str | Path) -> dict:
    """`output/shadow_vs_official.json` — actual fields from
    `shadow_vs_official_report.py`:
        jaccard_top25, spearman_full,
        verdict_official, verdict_shadow,
        ev_per_day_official, ev_per_day_shadow,
        recommendation.
    """
    data = read_json(Path(out_dir) / "shadow_vs_official.json")
    rec = str(data.get("recommendation") or "")
    rec_low = rec.lower()
    if "shadow leads" in rec_low:
        champion = "shadow"
    elif "official still leads" in rec_low or "keep current champion" in rec_low:
        champion = "official"
    else:
        champion = "review"
    return {
        "jaccard_top25": data.get("jaccard_top25"),
        "spearman_full": data.get("spearman_full"),
        "verdict_official": data.get("verdict_official"),
        "verdict_shadow": data.get("verdict_shadow"),
        "ev_per_day_official": data.get("ev_per_day_official"),
        "ev_per_day_shadow": data.get("ev_per_day_shadow"),
        "recommendation": rec or None,
        "champion": champion,
        "empty": not data,
    }


def read_news_digest(out_dir: str | Path) -> dict:
    """`output/news_digest.json` — human-context only.

    Returns: {schema_version, generated_at, refresh_status, ranking_column,
              disclaimer, counts (dict), stories (list[dict]),
              candidate_coverage (list[dict]), source_health (list[dict]),
              empty (bool)}.
    """
    data = read_json(Path(out_dir) / "news_digest.json")
    return {
        "schema_version": data.get("schema_version"),
        "generated_at": data.get("generated_at"),
        "refresh_status": data.get("refresh_status") or "unknown",
        "ranking_column": data.get("ranking_column") or "Confidence_Adjusted_Score",
        "disclaimer": data.get("disclaimer") or "",
        "counts": data.get("counts") or {},
        "stories": list(data.get("stories") or []),
        "candidate_coverage": list(data.get("candidate_coverage") or []),
        "source_health": list(data.get("source_health") or []),
        "empty": not data,
    }


def stories_for_symbol(digest: dict, symbol: str) -> list[dict]:
    """Filter news_digest.stories to a single symbol (case-insensitive)."""
    if not digest or not digest.get("stories"):
        return []
    s = (symbol or "").strip().upper()
    if not s:
        return []
    out = []
    for it in digest["stories"]:
        if str(it.get("Symbol", "")).strip().upper() == s:
            out.append(it)
    return out


__all__ = [
    "COLUMN_ALIASES", "pick_column",
    "read_json", "read_csv",
    "read_validation_status", "read_rebalance_diff", "read_daily_changes",
    "read_data_health", "read_shadow_summary", "read_news_digest",
    "stories_for_symbol",
]
