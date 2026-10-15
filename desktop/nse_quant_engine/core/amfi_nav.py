"""AMFI NAVAll fetch + parse — ONE implementation for the three ETF modules.

WHY THIS FILE EXISTS
--------------------
etf_quality_builder, etf_metadata_enricher and etf_aum_auto_fetcher each had
their own copy of "GET NAVAll.txt, split on ';'". On 2026-10-15 both AMFI URLs
answered HTTP 200 in about a second and all three parsers produced 0 rows. The
enricher logged `EMPTY` and moved on; nothing recorded WHAT the server sent.
Meanwhile amfi_navall_latest.csv had been a 2-byte file since 2026-08-20, so
there was no cache to fall back on, and every ETF in latest_scores.csv has had
NAV = blank and Mapping_Status = Missing since at least 2026-09-30.

That cascade is the whole Data Health page: no NAV table -> no scheme-code
mapping (UNRESOLVED_MAPPING 353/353) -> TER/TE imports cannot join on scheme
code (MISSING_TRACKING) -> NAV premium/discount is NaN for every ETF.

What this module changes:
  * browser-grade headers (a bare "Mozilla/5.0" UA is a common bot-filter trip)
  * BOM-safe decoding from bytes, not requests' charset guess
  * delimiter detection (';' today, but '|' / tab will not silently yield 0)
  * a scheme-code sanity check so an HTML page can never "parse"
  * a per-URL diagnostic — status, final URL, content type, bytes, and the
    first 200 characters of the body — so a 0-row response explains itself
  * the raw body of the last failed response saved to
    data/amfi_navall_last_failed_response.txt for inspection

It does NOT write any cache. Callers keep their own never-downgrade rules.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

NAVALL_URLS: tuple[str, ...] = (
    "https://portal.amfiindia.com/spages/NAVAll.txt",
    "https://www.amfiindia.com/spages/NAVAll.txt",
)

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/plain,text/html;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer": "https://www.amfiindia.com/net-asset-value",
    "Connection": "keep-alive",
}

COLUMNS = ["Scheme_Code", "ISIN_1", "ISIN_2", "Scheme_Name", "NAV", "NAV_Date"]
_SCHEME_CODE = re.compile(r"^\d{3,7}$")
FAILED_BODY_NAME = "amfi_navall_last_failed_response.txt"


def _to_float(x) -> float | None:
    s = str(x).strip().replace(",", "")
    if not s or s.upper() in {"N.A.", "NA", "N/A", "-"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_navall_text(text: str) -> pd.DataFrame:
    """Parse NAVAll text into COLUMNS. Returns an empty frame, never raises."""
    if not text:
        return pd.DataFrame(columns=COLUMNS)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Pick the delimiter that splits the most lines into >= 6 fields.
    best, best_hits = ";", -1
    for d in (";", "|", "\t"):
        hits = sum(1 for ln in lines[:4000] if ln.count(d) >= 5)
        if hits > best_hits:
            best, best_hits = d, hits
    rows = []
    for ln in lines:
        parts = [p.strip() for p in ln.split(best)]
        if len(parts) < 6:
            continue
        code, isin1, isin2, name, nav, nav_date = parts[:6]
        # Header rows, AMC/category banner lines and HTML all fail this test —
        # which is what stops a block page from ever counting as data.
        if not _SCHEME_CODE.match(code):
            continue
        nav_val = _to_float(nav)
        if nav_val is None:
            continue
        rows.append({"Scheme_Code": code, "ISIN_1": isin1, "ISIN_2": isin2,
                     "Scheme_Name": name, "NAV": nav_val, "NAV_Date": nav_date})
    return pd.DataFrame(rows, columns=COLUMNS)


def _head(text: str, n: int = 200) -> str:
    return re.sub(r"\s+", " ", text or "")[:n]


def fetch_navall(timeout: int = 45, data_dir: str | Path | None = None,
                 session=None) -> tuple[pd.DataFrame, list[dict]]:
    """Try each URL; return (frame, diagnostics). Frame is empty on failure."""
    import requests
    sess = session or requests.Session()
    diags: list[dict] = []
    last_body = None
    for url in NAVALL_URLS:
        d = {"url": url, "status": None, "final_url": None, "content_type": None,
             "bytes": 0, "rows": 0, "error": None, "head": ""}
        try:
            r = sess.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
            d.update(status=r.status_code, final_url=r.url,
                     content_type=r.headers.get("Content-Type"),
                     bytes=len(r.content or b""))
            text = (r.content or b"").decode("utf-8-sig", errors="replace")
            d["head"] = _head(text)
            if r.status_code != 200:
                d["error"] = f"HTTP {r.status_code}"
                last_body = text
                diags.append(d)
                continue
            df = parse_navall_text(text)
            d["rows"] = int(len(df))
            diags.append(d)
            if not df.empty:
                return df, diags
            d["error"] = "0 parseable NAV rows"
            last_body = text
        except Exception as exc:                         # network / DNS / TLS
            d["error"] = f"{type(exc).__name__}: {exc}"
            diags.append(d)
    if data_dir is not None and last_body:
        try:
            (Path(data_dir) / FAILED_BODY_NAME).write_text(
                last_body[:200_000], encoding="utf-8", errors="replace")
        except Exception:
            pass
    return pd.DataFrame(columns=COLUMNS), diags


def describe(diags: list[dict]) -> str:
    """One line per URL, for logs."""
    out = []
    for d in diags:
        out.append(f"{d['url']} -> status={d['status']} rows={d['rows']} "
                   f"type={d['content_type']} bytes={d['bytes']} "
                   f"final={d['final_url']} err={d['error']} head='{d['head'][:120]}'")
    return " | ".join(out)
