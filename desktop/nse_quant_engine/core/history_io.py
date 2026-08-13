"""Rolling-history append with a dedup key that actually deduplicates.

Extracted from nse_quant_engine.py so it can be tested without importing the
scoring engine (which requires yfinance at module import and therefore cannot be
imported in CI or on a machine without network dependencies installed).

THE BUG THIS EXISTS TO PREVENT
------------------------------
`drop_duplicates` compares raw values. A history file round-trips through
`read_csv`, so its dates come back as the string "2026-08-12", while freshly
built rows still hold `datetime.date(2026, 8, 12)` or a `pd.Timestamp`. Those are
not equal, so nothing was recognised as a duplicate and EVERY re-run of the
pipeline on the same date appended a complete duplicate row set (one row per
instrument) to score_history / signal_history / alpha_score_history.

That is not merely file bloat. `cross_sectional_validation.make_detail()` merges
forward returns against score history on (Signal_Date, Symbol). Duplicate keys
make that merge fan out, so each matured observation is counted once per re-run.
Duplicated observations inflate Obs, shrink standard errors and overstate
t-statistics — pseudo-replication that manufactures apparent edge out of nothing
but pressing "Run Full Pipeline" twice.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

__all__ = ["canonicalise_key", "append_history"]


def canonicalise_key(series: pd.Series, col_name: str) -> pd.Series:
    """Canonical string form of a dedup key column.

    Date columns collapse to ``YYYY-MM-DD`` so that a date object, a Timestamp
    and the string form all compare equal. Timestamp columns are compared as
    exact strings. Unparseable values keep their original text rather than
    becoming NaN, which would silently merge unrelated rows.
    """
    lowered = col_name.lower()
    # Only DATE columns are normalised to a day. Timestamp columns keep their
    # full value: run_log.csv is keyed on Run_Timestamp and must retain one row
    # per run, so collapsing it to a day would discard run history.
    if lowered == "date" or lowered.endswith("_date") or lowered.startswith("date_"):
        parsed = pd.to_datetime(series, errors="coerce")
        out = parsed.dt.strftime("%Y-%m-%d")
        return out.where(parsed.notna(), series.astype(str))
    return series.astype(str)


def append_history(file_path: str | Path, new_rows: pd.DataFrame,
                   key_cols: list[str], verbose: bool = True) -> pd.DataFrame:
    """Append `new_rows` to the history at `file_path`, deduped on `key_cols`.

    The most recent row wins per key. Canonicalising the key on both sides fixes
    duplication going forward AND self-heals files that already accumulated
    duplicates: they collapse on the next write.

    Returns the frame that was written.
    """
    file_path = Path(file_path)
    if file_path.exists():
        old = pd.read_csv(file_path)
        combined = pd.concat([old, new_rows], ignore_index=True)
    else:
        combined = new_rows.copy()

    present_keys = [c for c in key_cols if c in combined.columns]
    for col in present_keys:
        combined[col] = canonicalise_key(combined[col], col)

    before = len(combined)
    if present_keys:
        combined = combined.drop_duplicates(subset=present_keys, keep="last")
    removed = before - len(combined)
    if removed and verbose:
        print(f"[history] {file_path.name}: collapsed {removed} duplicate row(s) "
              f"on {present_keys} (keeping most recent per key)")

    file_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(file_path, index=False)
    return combined
