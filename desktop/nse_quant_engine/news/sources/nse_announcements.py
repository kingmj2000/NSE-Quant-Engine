"""NSE corporate announcements adapter — best-effort, cache-friendly.

Design:
- Warm ONE session per run (not per symbol).
- Fetch the equities announcements feed ONCE per run for a bounded date
  window; then map each candidate symbol against the returned feed. Never
  hits NSE per-candidate.
- Conservative timeouts and at most one retry.
- Does NOT bypass any anti-bot control: uses standard browser headers, warms
  cookies, and stops on 4xx/5xx.
- On block/failure the caller must fall back to cache.
"""
from __future__ import annotations

import time
from typing import Iterable
import pandas as pd
import requests

FEED_URL = "https://www.nseindia.com/api/corporate-announcements?index=equities"
WARMUP_URL = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/122.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


def warm_session(timeout: int = 10) -> requests.Session | None:
    try:
        s = requests.Session()
        s.headers.update(BROWSER_HEADERS)
        s.get("https://www.nseindia.com/", timeout=timeout)
        time.sleep(0.4)
        s.get(WARMUP_URL, timeout=timeout)
        time.sleep(0.4)
        return s
    except Exception:
        return None


def fetch_feed(session: requests.Session | None, window_days: int = 14, timeout: int = 12) -> tuple[list[dict], dict]:
    """Fetch announcement feed once per run. Returns (items, health)."""
    if session is None:
        return [], {"fetch_status": "failed", "items_received": 0, "error": "no_session"}
    for attempt in (1, 2):
        try:
            r = session.get(FEED_URL, timeout=timeout)
            if r.status_code >= 400:
                if attempt == 2:
                    return [], {"fetch_status": "failed", "items_received": 0, "error": f"HTTP {r.status_code}"}
                time.sleep(0.8)
                continue
            data = r.json()
            break
        except Exception as exc:
            if attempt == 2:
                return [], {"fetch_status": "failed", "items_received": 0, "error": f"{type(exc).__name__}: {exc}"}
            time.sleep(0.8)

    raw = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=window_days)
    items: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        sym = str(entry.get("symbol") or entry.get("Symbol") or "").strip().upper()
        title = str(entry.get("desc") or entry.get("subject") or entry.get("attchmntText") or "").strip()
        url = str(entry.get("attchmntFile") or entry.get("link") or "").strip()
        pub = pd.to_datetime(entry.get("an_dt") or entry.get("sort_date") or entry.get("dt"), errors="coerce")
        if not sym or not title:
            continue
        if pd.notna(pub) and pub < cutoff:
            continue
        items.append({
            "Filing_Symbol": sym,
            "Canonical_Title": title,
            "URL": url,
            "Source": "NSE",
            "Source_Type": "official_filing",
            "Is_Official_Filing": True,
            "Published_Date": pub if pd.notna(pub) else pd.NaT,
        })
    return items, {"fetch_status": "success", "items_received": len(items), "error": ""}


def filter_for_symbols(items: list[dict], symbols: Iterable[str]) -> dict[str, list[dict]]:
    """Group announcement items by symbol using exchange identifier mapping."""
    wanted = {str(s).strip().upper() for s in symbols if s}
    out: dict[str, list[dict]] = {s: [] for s in wanted}
    for it in items:
        sym = it.get("Filing_Symbol", "")
        if sym in wanted:
            out[sym].append(it)
    return out
