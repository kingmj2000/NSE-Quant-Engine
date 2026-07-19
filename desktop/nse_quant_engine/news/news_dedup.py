"""Global deduplication across queries and sources.

Cluster key cascade (first hit wins):
  1. Canonical URL (scheme+host+path, tracking params dropped)
  2. Normalized title (lowercased, punctuation stripped, whitespace collapsed)
  3. Conservative fuzzy title similarity (token Jaccard ≥ 0.9)

(Symbol, Event_Category, Published_Date) is NOT an independent key. It may
only reinforce (2) or (3); distinct same-day filings/stories with different
titles must remain separate.

Official filings are never merged into media rows — they cluster only with
other filings sharing the same URL/title. Media items referring to the same
event are linked via Related_URLs.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse
import pandas as pd

_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid"}


def canonical_url(url: str) -> str:
    if not url or not isinstance(url, str):
        return ""
    try:
        p = urlparse(url.strip())
    except Exception:
        return url.strip()
    host = (p.netloc or "").lower().replace("www.", "")
    path = re.sub(r"/+$", "", p.path or "")
    return urlunparse((p.scheme or "https", host, path, "", "", ""))


def normalize_title(title: str) -> str:
    if not title:
        return ""
    s = re.sub(r"[^A-Za-z0-9\s]+", " ", title.lower())
    return re.sub(r"\s+", " ", s).strip()


def _title_tokens(title: str) -> set[str]:
    return {t for t in normalize_title(title).split() if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def dedup(rows: pd.DataFrame, fuzzy_threshold: float = 0.9) -> pd.DataFrame:
    """Cluster rows, return one representative per cluster with All_Sources,
    Duplicate_Count. Official filings cluster among themselves only."""
    if rows is None or rows.empty:
        return rows.copy() if rows is not None else pd.DataFrame()

    df = rows.copy().reset_index(drop=True)
    df["_url_key"] = df["URL"].map(canonical_url)
    df["_title_key"] = df["Canonical_Title"].map(normalize_title)
    df["_tokens"] = df["Canonical_Title"].map(_title_tokens)
    df["_is_filing"] = df.get("Is_Official_Filing", False).astype(bool)

    cluster_id = [-1] * len(df)
    reps: list[int] = []

    for i in range(len(df)):
        if cluster_id[i] != -1:
            continue
        cluster_id[i] = i
        reps.append(i)
        for j in range(i + 1, len(df)):
            if cluster_id[j] != -1:
                continue
            # filings never merge into media (and vice versa)
            if df.at[i, "_is_filing"] != df.at[j, "_is_filing"]:
                continue
            same_url = df.at[i, "_url_key"] and df.at[i, "_url_key"] == df.at[j, "_url_key"]
            same_title = df.at[i, "_title_key"] and df.at[i, "_title_key"] == df.at[j, "_title_key"]
            fuzzy = _jaccard(df.at[i, "_tokens"], df.at[j, "_tokens"]) >= fuzzy_threshold
            if same_url or same_title or fuzzy:
                cluster_id[j] = i

    df["_cluster"] = cluster_id
    out_rows = []
    for cid, grp in df.groupby("_cluster", sort=False):
        # pick primary: prefer official filings, then earliest First_Seen,
        # then earliest Published_Date
        grp = grp.copy()
        grp["_pd"] = pd.to_datetime(grp["Published_Date"], errors="coerce")
        grp["_fs"] = pd.to_datetime(grp["First_Seen"], errors="coerce")
        grp = grp.sort_values(
            by=["_is_filing", "_fs", "_pd"],
            ascending=[False, True, True],
            na_position="last",
        )
        primary = grp.iloc[0].copy()
        sources = [str(s) for s in grp["Source"].fillna("").tolist() if str(s)]
        # dedup while preserving order
        seen = set(); ordered = []
        for s in sources:
            if s not in seen:
                seen.add(s); ordered.append(s)
        primary["All_Sources"] = " | ".join(ordered)
        primary["Duplicate_Count"] = int(len(grp))
        out_rows.append(primary)

    out = pd.DataFrame(out_rows)
    return out.drop(columns=[c for c in ("_url_key", "_title_key", "_tokens", "_is_filing", "_cluster", "_pd", "_fs") if c in out.columns]).reset_index(drop=True)


def cluster_key(row: pd.Series) -> str:
    u = canonical_url(str(row.get("URL", "")))
    if u:
        return f"u::{u}"
    t = normalize_title(str(row.get("Canonical_Title", "")))
    sym = str(row.get("Symbol", "")).upper()
    return f"t::{sym}::{t}"
