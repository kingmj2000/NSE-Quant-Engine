"""Google News RSS adapter — best-effort, network-optional.

On any failure, returns an empty list AND a source-health dict entry via the
caller's SourceHealth registry. A failure never produces a fake headline row.
"""
from __future__ import annotations

from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
import xml.etree.ElementTree as ET
import requests
import pandas as pd

HEADERS = {"User-Agent": "Mozilla/5.0"}


def rss_url(query: str, recent_days: int = 30) -> str:
    q = f"{query} when:{recent_days}d"
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"


def _parse_pub(value: str):
    if not value:
        return pd.NaT
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return pd.Timestamp(dt)
    except Exception:
        return pd.NaT


def fetch(query: str, recent_days: int = 30, limit: int = 10, timeout: int = 20) -> tuple[list[dict], dict]:
    """Return (items, health). health has fetch_status/items_received/error."""
    health = {"fetch_status": "success", "items_received": 0, "error": ""}
    try:
        r = requests.get(rss_url(query, recent_days), headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as exc:
        return [], {"fetch_status": "failed", "items_received": 0, "error": f"{type(exc).__name__}: {exc}"}

    items = []
    seen = set()
    for it in root.findall("./channel/item"):
        title = (it.findtext("title") or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        link = (it.findtext("link") or "").strip()
        pub_raw = (it.findtext("pubDate") or "").strip()
        src_node = it.find("source")
        source = (src_node.text if src_node is not None else "") or "Google News"
        items.append({
            "Query": query,
            "Canonical_Title": title,
            "URL": link,
            "Source": source,
            "Source_Type": "media",
            "Is_Official_Filing": False,
            "Published_Raw": pub_raw,
            "Published_Date": _parse_pub(pub_raw),
        })
        if len(items) >= limit:
            break
    health["items_received"] = len(items)
    return items, health
