"""
Raw-price cache (clean core v4).

Why this exists — two problems in one fix:

1. SPEED: the old engine re-downloaded ~2 years x 430 symbols every run to use
   mostly the last ~250 days. This caches raw prices once and appends only new
   trading days on subsequent runs.

2. CORRECTNESS: yfinance re-adjusts the ENTIRE history on every pull (a new
   dividend/split re-bases all past adjusted closes). That silently changes the
   forward-return comparison the validation layer depends on. By caching a
   stable price series and only appending, a signal's entry price stays fixed
   on the same basis as its forward price.

This module is storage/merge logic only. The actual yfinance call stays in your
proven download wrapper — you pass its output in via `update_cache`. That keeps
the network-dependent, hard-to-test piece in your battle-tested code while the
testable merge logic lives here.

Cache schema (long form):
    Date, Symbol, Price        # Price = adjusted close at time of first capture
"""

from __future__ import annotations
from pathlib import Path
import pandas as pd


def load_cache(cache_path: str | Path) -> pd.DataFrame:
    p = Path(cache_path)
    if not p.exists():
        return pd.DataFrame(columns=["Date", "Symbol", "Price"])
    try:
        df = pd.read_csv(p)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=["Date", "Symbol", "Price"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce")
    return df.dropna(subset=["Date", "Symbol", "Price"])


def update_cache(
    cache: pd.DataFrame,
    fresh: pd.DataFrame,
    freeze_history: bool = True,
) -> pd.DataFrame:
    """
    Merge freshly downloaded prices into the cache.

    fresh: long-form DataFrame with columns Date, Symbol, Price (from your
           yfinance wrapper, already melted to long form).

    freeze_history=True (default): existing (Date, Symbol) prices are NEVER
        overwritten — only genuinely new (Date, Symbol) rows are appended. This
        is what gives you adjustment-stability: a price captured last week keeps
        last week's basis even if yfinance re-adjusts it today.

    freeze_history=False: fresh values overwrite (use only for a deliberate
        full rebuild, e.g. after a known split you want to re-baseline).
    """
    if fresh is None or fresh.empty:
        return cache.copy()

    fresh = fresh.copy()
    fresh["Date"] = pd.to_datetime(fresh["Date"], errors="coerce")
    fresh["Price"] = pd.to_numeric(fresh["Price"], errors="coerce")
    fresh = fresh.dropna(subset=["Date", "Symbol", "Price"])

    if cache is None or cache.empty:
        merged = fresh
    elif freeze_history:
        # Keep all existing rows; append only (Date, Symbol) pairs not present.
        key_existing = set(zip(cache["Date"], cache["Symbol"]))
        mask_new = [
            (d, s) not in key_existing
            for d, s in zip(fresh["Date"], fresh["Symbol"])
        ]
        merged = pd.concat([cache, fresh[mask_new]], ignore_index=True)
    else:
        # Overwrite: fresh wins on collision.
        combined = pd.concat([cache, fresh], ignore_index=True)
        merged = combined.drop_duplicates(subset=["Date", "Symbol"], keep="last")

    return merged.sort_values(["Symbol", "Date"]).reset_index(drop=True)


def save_cache(cache: pd.DataFrame, cache_path: str | Path) -> None:
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    cache.to_csv(cache_path, index=False)


def symbols_needing_download(
    cache: pd.DataFrame,
    universe_symbols: list[str],
    today: pd.Timestamp,
    max_stale_days: int = 1,
) -> list[str]:
    """
    Return symbols whose cache is missing or stale enough to need a fresh pull.
    Lets the caller download only what's needed instead of the whole universe.
    """
    if cache is None or cache.empty:
        return list(universe_symbols)
    last_seen = cache.groupby("Symbol")["Date"].max()
    needs = []
    for sym in universe_symbols:
        if sym not in last_seen.index:
            needs.append(sym)
        elif (today - last_seen[sym]).days > max_stale_days:
            needs.append(sym)
    return needs


def wide_window(cache: pd.DataFrame, lookback_days: int = 400) -> pd.DataFrame:
    """Return the recent window as a Date-indexed wide frame for indicator math."""
    if cache is None or cache.empty:
        return pd.DataFrame()
    cutoff = cache["Date"].max() - pd.Timedelta(days=lookback_days)
    recent = cache[cache["Date"] >= cutoff]
    return recent.pivot_table(index="Date", columns="Symbol", values="Price")
