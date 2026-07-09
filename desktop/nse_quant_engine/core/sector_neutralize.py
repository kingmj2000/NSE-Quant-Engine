"""
Sector-neutral scoring (Part A2 of the tightened plan).

Removes sector-mean drift from raw factor scores so momentum in a hot sector
does not automatically outrank momentum in a cold one. Skips sectors with
fewer than `min_members` members — those symbols stay universe-standardized
only. All skipped sectors are logged (name + member count) so the run's
`scoring_sector_neutralization.csv` artifact tells you exactly what happened.

Pure functions, no I/O — the caller writes the artifact.
"""
from __future__ import annotations

from typing import Iterable
import numpy as np
import pandas as pd


def _zscore(s: pd.Series) -> pd.Series:
    s = pd.to_numeric(s, errors="coerce")
    m = s.mean(skipna=True)
    sd = s.std(ddof=0, skipna=True)
    if not sd or np.isnan(sd) or sd == 0:
        # nothing to standardize — return centred (or zeros)
        return s - m if pd.notna(m) else s * 0.0
    return (s - m) / sd


def neutralize(df: pd.DataFrame,
               factor_cols: Iterable[str],
               sector_col: str = "Sector",
               min_members: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (neutralized_df, audit_df).

    For each column in `factor_cols`:
      * inside every sector with >= min_members members, z-score members;
      * for sectors below the threshold, leave values as-is (they will still
        be universe-standardized in the final step);
      * finally re-standardize universe-wide so scales match across factors.

    audit_df columns:
      Symbol, Sector, Sector_Size, Skipped, Raw_<factor>, Neutralized_<factor>
    """
    if df is None or df.empty or sector_col not in df.columns:
        return df.copy() if df is not None else pd.DataFrame(), pd.DataFrame(
            columns=["Symbol", "Sector", "Sector_Size", "Skipped"])

    out = df.copy()
    sizes = out.groupby(sector_col)[sector_col].transform("size")
    skipped_mask = sizes < int(min_members)

    audit_cols = {
        "Symbol": out.get("Symbol"),
        "Sector": out[sector_col],
        "Sector_Size": sizes.astype(int),
        "Skipped": skipped_mask.astype(bool),
    }

    for col in factor_cols:
        if col not in out.columns:
            continue
        raw = pd.to_numeric(out[col], errors="coerce")
        audit_cols[f"Raw_{col}"] = raw

        neut = pd.to_numeric(raw, errors="coerce").astype(float).copy()
        for sec, g in out.groupby(sector_col):
            if sizes.loc[g.index].iloc[0] < min_members:
                continue
            neut.loc[g.index] = _zscore(neut.loc[g.index]).astype(float)
        audit_cols[f"SectorZ_{col}"] = neut.copy()   # per-sector z (before universe rescale)

        # Universe-wide re-standardization keeps skipped-sector symbols
        # comparable to neutralized ones.
        final = _zscore(neut).astype(float)
        out[col] = final
        audit_cols[f"Neutralized_{col}"] = final

    audit = pd.DataFrame(audit_cols)
    return out, audit


def skipped_sector_log(audit: pd.DataFrame) -> pd.DataFrame:
    """Small helper for the log line: sector, member count, skipped_yes/no."""
    if audit is None or audit.empty:
        return pd.DataFrame(columns=["Sector", "Sector_Size", "Skipped"])
    return (audit[["Sector", "Sector_Size", "Skipped"]]
            .drop_duplicates(subset=["Sector"])
            .sort_values(["Skipped", "Sector_Size"], ascending=[False, True])
            .reset_index(drop=True))
