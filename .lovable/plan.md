## Problem

Two of the four auto-fetchers are failing on every run:

1. **FII/DII (Moneycontrol)** — `pandas.read_html` needs a parser. Behind the scenes it tries `lxml` first, but on this Windows install pandas is falling through to `html5lib` and raising `ImportError: Missing optional dependency 'html5lib'`. Root cause: we never pass `flavor="lxml"` explicitly, and the Moneycontrol page has malformed markup that lxml rejects — pandas then asks for html5lib.
2. **Bulk deals (NSE)** — the `nseindia.com/api/historical/bulk-deals` endpoint returns HTTP 503. NSE aggressively blocks non-browser clients from the cloud/office IP ranges and rate-limits repeat callers. A single cookie warm-up is not enough anymore.

Both need a more robust fetch strategy with real fallbacks, not just "fail quiet".

## Fix

### 1. `core/optional_data_fetchers.py` — FII/DII

- Add `html5lib>=1.1` to `requirements.txt` so `pandas.read_html` always has a fallback parser (tiny pure-Python dep, no compile).
- Rewrite `fetch_fii_dii` to try sources in order and return the first that yields a parseable table with rows in the last ~10 days:
  1. **NSDL FPI daily** — `https://www.fpi.nsdl.co.in/web/Reports/Daily.aspx` (form POST for date range). Provides FII net; DII filled from source 2.
  2. **Moneycontrol FII/DII activity** — same URL, but call `pd.read_html(text, flavor="lxml")` first, then `flavor="html5lib"` on `ValueError`, and `flavor="bs4"` last. Wrap in `try/except` per flavor.
  3. **Trendlyne / Groww public FII-DII JSON** as final fallback (no key, browser UA).
- Column detection stays heuristic (already handles multi-index headers).
- Log which source succeeded: `[fetch] fii_dii via nsdl` / `via moneycontrol` / `via trendlyne`.

### 2. `core/optional_data_fetchers.py` — Bulk deals

Replace the single NSE JSON call with a 3-tier strategy:

1. **NSE CSV report** — `https://archives.nseindia.com/content/equities/bulk.csv` (static daily CSV, mirrors the JSON, and does NOT require the cookie handshake). This is the reliable primary source most OSS libs use.
2. **NSE JSON API** — keep current path as fallback, but with:
   - proper cookie chain: hit `nseindia.com` → `market-data/live-equity-market` → the API, with `Referer` + `Sec-Fetch-*` headers.
   - retry once after a 2s sleep on 5xx.
3. **BSE bulk deals CSV** — `https://www.bseindia.com/markets/equity/EQReports/bulk_deals.aspx` for cross-exchange coverage (optional final fallback; NSE-only symbols will still map correctly).

Normalize all three to the existing `Date, Symbol, Client, Buy_Sell, Qty, Price` schema. The daily CSV only covers the current day, so we merge with the existing 60-day cache (already handled by `_merge_dated`).

### 3. Small polish

- Add `[fetch] fii_dii source attempted: <name>` warning line for each failed source, then final `[fetch][warn]` only if ALL sources fail — makes the log actionable instead of blaming one source.
- Add unit-friendly small helper `_try_read_html(text)` that walks the flavor list.
- No change to freshness windows, cache policy, pipeline wiring, or UI.

### Files touched

- edit `desktop/nse_quant_engine/core/optional_data_fetchers.py` (rewrite `fetch_fii_dii` and `fetch_bulk_deals`, add helpers)
- edit `desktop/nse_quant_engine/requirements.txt` (+ `html5lib>=1.1`)
- edit `desktop/nse_quant_engine/CHANGES_v4_3.md` (note the fetcher hardening)

### Out of scope

- No changes to the other 15 pipeline steps, UI, dashboard, or any scoring logic.
- If NSE blocks the office IP entirely (corporate proxy / geo-block), even the archives CSV can 403 — in that case the pipeline still runs quiet with a clear log line, and the user can drop `data/bulk_deals.csv` manually as before.

### Verification

After the change, next run's log should show either:

```
[fetch] fii_dii via moneycontrol (32 rows)
[fetch] bulk_deals via nse-archives (18 rows today, 412 in cache)
```

or, if a source is down, a specific `attempted → failed → next` chain instead of a single opaque error.
