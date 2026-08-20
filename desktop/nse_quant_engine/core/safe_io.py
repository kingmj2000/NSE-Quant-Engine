"""Safe reads for cached/derived CSV artifacts.

WHY THIS EXISTS
---------------
`Path.exists()` is not the same as "this file is usable". A zero-byte or
truncated CSV passes the exists check and then makes `pd.read_csv` raise
`EmptyDataError: No columns to parse from file`. When that file is a *cache* —
something the code falls back to precisely because the network already failed —
the crash lands in the recovery path, which is the worst possible place for it.

That is exactly how a transient DNS outage halted the whole pipeline: an empty
`amfi_navall_latest.csv` was written during the outage, and every later run died
reading it, even after the network recovered.

Two readers, and the distinction matters:

  * `read_cached_csv`   — optional/derived data. Missing, empty or corrupt all
                          mean "no data": return an empty frame, say so once, and
                          let the caller degrade.
  * `read_required_csv` — genuine pipeline inputs (config.csv and friends).
                          Still raises, but with a message naming the file and
                          the step that produces it, instead of a pandas
                          traceback.

A corrupt cache is never silently deleted. It is simply treated as absent, so the
next successful fetch overwrites it and the problem self-heals.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

__all__ = ["read_cached_csv", "read_required_csv", "is_usable_csv"]


def is_usable_csv(path: str | Path) -> bool:
    """True when `path` exists and holds at least a header row."""
    p = Path(path)
    try:
        return p.is_file() and p.stat().st_size > 0
    except Exception:
        return False


def read_cached_csv(path: str | Path,
                    expected_columns: list[str] | None = None,
                    label: str | None = None,
                    quiet: bool = False,
                    **read_kwargs) -> pd.DataFrame:
    """Read a cached artifact, returning an empty frame if it is unusable.

    Never raises. When `expected_columns` is given, the returned frame always has
    those columns, so callers can index them without another guard.
    """
    p = Path(path)
    name = label or p.name

    def _empty() -> pd.DataFrame:
        return pd.DataFrame(columns=expected_columns) if expected_columns else pd.DataFrame()

    if not is_usable_csv(p):
        if p.exists() and not quiet:
            print(f"WARNING: cached {name} is empty — treating as absent. "
                  f"It will be rewritten by the next successful fetch.")
        return _empty()

    try:
        df = pd.read_csv(p, **read_kwargs)
    except pd.errors.EmptyDataError:
        if not quiet:
            print(f"WARNING: cached {name} has no parseable columns — treating as "
                  f"absent. It will be rewritten by the next successful fetch.")
        return _empty()
    except Exception as exc:
        if not quiet:
            print(f"WARNING: cached {name} could not be read "
                  f"({type(exc).__name__}: {exc}) — treating as absent.")
        return _empty()

    if expected_columns:
        for col in expected_columns:
            if col not in df.columns:
                df[col] = ""
    return df


def read_required_csv(path: str | Path, produced_by: str = "",
                      **read_kwargs) -> pd.DataFrame:
    """Read a genuine pipeline input. Raises, but with an actionable message."""
    p = Path(path)
    hint = f" Run {produced_by} first." if produced_by else ""
    if not p.exists():
        raise FileNotFoundError(f"{p.name} not found.{hint}")
    if not is_usable_csv(p):
        raise ValueError(
            f"{p.name} exists but is empty — it was probably written during a "
            f"failed run. Delete it and regenerate.{hint}")
    try:
        return pd.read_csv(p, **read_kwargs)
    except pd.errors.EmptyDataError as exc:
        raise ValueError(
            f"{p.name} has no parseable columns — it was probably written during "
            f"a failed run. Delete it and regenerate.{hint}") from exc
