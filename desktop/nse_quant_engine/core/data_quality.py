"""Structured data-quality flag taxonomy for ETF metadata + helpers."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
import pandas as pd

# Fixed enum — every flag below replaces the noisy free-text ETF_Quality_Data_Flag.
# Actionable = something we can fix by improving fetchers / mapping.
# Structural = source simply does not publish it (e.g. AMFI does not give
#   point-in-time tracking error for most NSE ETFs); penalising health for
#   these creates false alarms.
FLAGS = (
    "OK",
    "MISSING_TER",            # actionable
    "MISSING_TRACKING",       # structural for many AMFI ETFs
    "STALE_NAV",              # actionable
    "LOW_AUM",                # informational
    "UNRESOLVED_MAPPING",     # actionable
    "PRICE_GAP",              # informational
)

STRUCTURAL_FLAGS = {"MISSING_TRACKING"}
ACTIONABLE_FLAGS = {"MISSING_TER", "STALE_NAV", "UNRESOLVED_MAPPING"}

LOW_AUM_CRORE = 25.0       # < 25 cr AUM flagged for liquidity risk
STALE_NAV_BDAYS = 5


def _is_missing(v) -> bool:
    if v is None:
        return True
    try:
        if isinstance(v, str) and not v.strip():
            return True
        return pd.isna(v)
    except Exception:
        return False


def _is_stale(nav_date, today: datetime | None = None) -> bool:
    if _is_missing(nav_date):
        return True
    today = today or datetime.today()
    try:
        d = pd.to_datetime(nav_date, errors="coerce", dayfirst=True)
        if pd.isna(d):
            return True
        # business-day distance — approximation
        return (pd.bdate_range(d, today).size - 1) > STALE_NAV_BDAYS
    except Exception:
        return True


def classify_row(row: dict, today: datetime | None = None) -> list[str]:
    flags: list[str] = []
    if str(row.get("Mapping_Status", "")).lower() not in ("verified", "manual", "matched"):
        flags.append("UNRESOLVED_MAPPING")
    if _is_missing(row.get("TER")):
        flags.append("MISSING_TER")
    if _is_missing(row.get("Tracking_Error")) and _is_missing(row.get("Tracking_Difference")):
        flags.append("MISSING_TRACKING")
    if _is_stale(row.get("NAV_Date"), today):
        flags.append("STALE_NAV")
    try:
        aum = float(row.get("AUM_Cr"))
        if aum < LOW_AUM_CRORE:
            flags.append("LOW_AUM")
    except Exception:
        pass
    return flags or ["OK"]


def annotate(df: pd.DataFrame, today: datetime | None = None) -> pd.DataFrame:
    """Add `Quality_Flags` (pipe-joined) plus one bool column per flag."""
    out = df.copy()
    flag_lists = [classify_row(r, today) for r in out.to_dict("records")]
    out["Quality_Flags"] = ["|".join(fl) for fl in flag_lists]
    for f in FLAGS:
        out[f"Flag_{f}"] = [f in fl for fl in flag_lists]
    return out


def fill_rate(df: pd.DataFrame, cols: Iterable[str]) -> dict[str, float]:
    rates = {}
    n = max(len(df), 1)
    for c in cols:
        if c not in df.columns:
            rates[c] = 0.0
            continue
        rates[c] = float(df[c].apply(lambda v: not _is_missing(v)).sum() / n)
    return rates


def health_score(df: pd.DataFrame, key_fields=("NAV", "TER", "AUM_Cr",
                                               "Benchmark_Index")) -> float:
    """Health score over *actionable* coverage only.

    Tracking_Error / Tracking_Difference are excluded by default because AMFI
    rarely publishes them for NSE ETFs — including them was producing a
    perpetual 60–70 score that never improved no matter how good our fetchers
    got. Use `coverage_breakdown()` for the full per-field picture.
    """
    rates = fill_rate(df, key_fields)
    weights = {"NAV": 1.5, "TER": 1.3, "AUM_Cr": 1.3, "Benchmark_Index": 0.7}
    num = sum(rates[c] * weights.get(c, 1.0) for c in key_fields)
    den = sum(weights.get(c, 1.0) for c in key_fields)
    return round(100.0 * num / den, 1)


def coverage_breakdown(df: pd.DataFrame) -> dict:
    """Per-field fill rates split into actionable vs structural buckets."""
    actionable = ["NAV", "TER", "AUM_Cr", "Benchmark_Index", "AMFI_Scheme_Code"]
    structural = ["Tracking_Error", "Tracking_Difference"]
    return {
        "actionable": fill_rate(df, actionable),
        "structural": fill_rate(df, structural),
    }

