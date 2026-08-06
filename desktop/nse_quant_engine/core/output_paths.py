"""Single source of truth for pipeline output filenames.

Producers (``trade_plan_builder.py`` and friends) and every consumer (desktop
UI tabs, evidence bundle, validation) MUST import these constants instead of
hard-coding strings, so a producer rename can never silently break the UI.
"""
from __future__ import annotations

from pathlib import Path

# ── Top-5 artifacts (all describe the same official symbol set) ─────────────
TOP5_POSITION_SIZING_CSV = "top5_position_sizing.csv"
TOP5_EVENTS_CSV = "top5_events.csv"
TOP5_SECTOR_CONTEXT_CSV = "top5_sector_context.csv"
TOP5_EXPECTED_VALUE_CSV = "top5_expected_value.csv"
TOP5_INSTITUTIONAL_FLOW_CSV = "top5_institutional_flow.csv"
TOP5_CORR_MATRIX_CSV = "top5_corr_matrix.csv"
TOP5_HORIZON_CSV = "top5_horizon.csv"
TOP5_FUNDAMENTALS_CSV = "top5_fundamentals.csv"
TOP5_BENCHMARK_STATS_CSV = "top5_benchmark_stats.csv"

# ── Core artifacts ──────────────────────────────────────────────────────────
LATEST_SCORES_CSV = "latest_scores.csv"
TRADE_PLAN_CSV = "trade_plan_latest.csv"
VALIDATION_STATUS_JSON = "validation_status.json"
PORTFOLIO_VALIDATION_JSON = "portfolio_validation.json"
MACRO_CONTEXT_JSON = "macro_context.json"
REGIME_TILT_JSON = "regime_tilt_report.json"
REBALANCE_DIFF_JSON = "rebalance_diff.json"
SHADOW_VS_OFFICIAL_JSON = "shadow_vs_official.json"

# Names the desktop UI reads directly; asserted by tests against producers.
UI_CONSUMED = (
    TOP5_POSITION_SIZING_CSV,
    TOP5_EVENTS_CSV,
    TOP5_SECTOR_CONTEXT_CSV,
    TOP5_EXPECTED_VALUE_CSV,
    TOP5_INSTITUTIONAL_FLOW_CSV,
    PORTFOLIO_VALIDATION_JSON,
)


def out_path(output_dir: str | Path, name: str) -> Path:
    return Path(output_dir) / name


__all__ = [n for n in dir() if n.isupper()] + ["out_path", "UI_CONSUMED"]
