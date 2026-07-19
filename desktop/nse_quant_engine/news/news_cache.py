"""Persistent news cache (article history).

The cache is article history only. It is NOT used to derive candidate
membership, rankings or eligibility — that comes from the finalized official
score source through core.candidate_selection.top_official_candidates and
authoritative rank-change outputs.

All writes are atomic (tmp file + os.replace).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable
import pandas as pd

CACHE_COLUMNS = [
    "Cluster_Key", "Symbol", "Rank", "Name",
    "Canonical_Title", "Source", "All_Sources", "Source_Type",
    "Published_Date", "Age_Days", "Recency_Bucket", "Event_Category",
    "URL", "Is_Official_Filing", "Relevance_Reason",
    "Duplicate_Count", "First_Seen", "Last_Seen", "Fetched_At",
]


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def atomic_write_df(path: Path, df: pd.DataFrame) -> None:
    atomic_write_text(Path(path), df.to_csv(index=False))


def read_cache(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return pd.DataFrame(columns=CACHE_COLUMNS)
    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame(columns=CACHE_COLUMNS)
    for c in CACHE_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA
    return df[CACHE_COLUMNS]


def upsert(existing: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    """Merge on Cluster_Key. Preserve First_Seen from existing rows."""
    if existing is None or existing.empty:
        return new_rows.copy() if new_rows is not None else pd.DataFrame(columns=CACHE_COLUMNS)
    if new_rows is None or new_rows.empty:
        return existing.copy()
    keep = existing.set_index("Cluster_Key")
    incoming = new_rows.set_index("Cluster_Key")
    # preserve First_Seen if we already had it
    common = incoming.index.intersection(keep.index)
    if len(common):
        incoming.loc[common, "First_Seen"] = keep.loc[common, "First_Seen"].where(
            keep.loc[common, "First_Seen"].notna(), incoming.loc[common, "First_Seen"]
        )
    keep.update(incoming)
    fresh = incoming.loc[~incoming.index.isin(keep.index)]
    combined = pd.concat([keep, fresh]).reset_index()
    return combined[CACHE_COLUMNS]


def prune(df: pd.DataFrame, media_days: int = 180, filing_days: int = 540) -> pd.DataFrame:
    """Keep official filings longer than ordinary media items. Only call after
    successful output generation."""
    if df is None or df.empty:
        return df
    now = pd.Timestamp.now()
    pub = pd.to_datetime(df["Published_Date"], errors="coerce")
    is_filing = df["Is_Official_Filing"].astype(str).str.lower().isin(["true", "1", "yes"])
    age_days = (now - pub).dt.days
    # keep unknown-date rows (age NaN) — safer to retain than lose
    keep_mask = (
        age_days.isna()
        | (is_filing & (age_days <= filing_days))
        | (~is_filing & (age_days <= media_days))
    )
    return df.loc[keep_mask].reset_index(drop=True)
