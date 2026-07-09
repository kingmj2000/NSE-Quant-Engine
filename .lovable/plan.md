
## Goal

Kill the manual CSV drops. Add auto-fetchers so `data/fii_dii_daily.csv`, `data/bulk_deals.csv`, `data/fundamentals_latest.csv`, and `data/earnings_calendar.csv` are populated at the start of every pipeline run from free public sources — same schemas the existing overlays already read, so no downstream code changes.

Design principle: **fail quiet, never break the pipeline.** If a source is down, we keep the last good cache and print a `[fetch]` warning. Overlays then run against whatever we have (possibly stale), and the activation checklist shows freshness (✅ fresh / 🟡 stale >7 days / ⚠ missing) instead of only present/missing.

## New module: `core/optional_data_fetchers.py`

One file, four independent functions, each ~40–80 lines. Called from `run_app.py` (and `run_full_workflow.bat`) as a new pre-step **Step 0.5** before universe build. Uses only libraries already in `requirements.txt` (`requests`, `pandas`, `beautifulsoup4`, `lxml`, `yfinance`).

| Function | Source | How | Notes |
|---|---|---|---|
| `fetch_fii_dii(data_dir, days=60)` | **NSDL** `https://www.fpi.nsdl.co.in/web/Reports/Yearwise.aspx` for FII, **Moneycontrol** `https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php` as fallback for DII | `requests` + `pandas.read_html` / `BeautifulSoup` table parse, normalize to `Date, FII_Net_INR_Cr, DII_Net_INR_Cr` | Both are HTML tables, no API key. Merge with existing CSV, dedupe by Date, keep last 90 rows. |
| `fetch_bulk_deals(data_dir, days=30)` | **NSE** `https://www.nseindia.com/api/historical/bulk-deals?from=DD-MM-YYYY&to=DD-MM-YYYY` (JSON) | `requests` with browser-like headers + cookie bootstrap (hit `nseindia.com` first to get cookies), map fields to `Date, Symbol, Client, Buy_Sell, Qty, Price` | NSE requires a cookie handshake — pattern is well known and used by many OSS projects. |
| `fetch_fundamentals(data_dir, symbols)` | **yfinance** `Ticker.info` (already used by `fundamental_factor.py`) | Reuse `core.fundamental_factor.fetch_fundamentals`, but write output to `data/fundamentals_latest.csv` in the schema `fundamentals_overlay.py` expects (`Symbol, ROE_TTM, DebtToEquity, EPS_Growth_YoY, PE_TTM, PEG, ProfitMargin, PromoterPledgePct, PE_Self_Median_3Y`) — map `ROE→ROE_TTM`, `PE→PE_TTM`, `EarningsGrowth→EPS_Growth_YoY`. Missing fields stay NaN. | Only refetches symbols older than N days in the cache (default 7) so a full run stays under ~2 min for the top-500 shortlist. |
| `fetch_earnings_calendar(data_dir, symbols)` | **yfinance** `Ticker.calendar` (returns next earnings date per ticker) | Loop the shortlist, collect `Symbol, Event_Date` where `Earnings Date` is present, write CSV | Free, no scraping. Only writes symbols that have a scheduled date within the next 90 days. |

Cache/staleness policy (per file):
- Fresh (<24h for flows, <7d for fundamentals/earnings) → skip fetch.
- Stale but present → refetch; on failure keep old file and mark 🟡.
- Missing → attempt fetch; on failure leave missing and mark ⚠.

All fetchers:
- 10-second per-request timeout, single retry with backoff.
- Wrapped in `try/except` — any exception logs `[fetch][warn] <source>: <msg>` and returns without raising.
- Never delete an existing file.

## Wiring

1. **`run_app.py`** — new "Refreshing optional data feeds…" phase before the universe build. Runs the four fetchers in sequence (they're I/O bound and fast). Emits progress lines to the existing log pane. Failure of any fetcher is non-fatal.
2. **`orchestrator.py`** — same call at the top of the pipeline so CLI runs behave identically.
3. **`run_full_workflow.bat`** — insert a `python -c "from core.optional_data_fetchers import refresh_all; refresh_all()"` line before `universe_builder.py`. Non-fatal (`if errorlevel 1` removed for this one step).
4. **Activation checklist panel in `run_app.py`** — upgrade the ⚠/✅ badges to show file mtime and row count (e.g. `✅ fii_dii_daily.csv · 342 rows · updated 2h ago`), plus a "🔄 Refresh now" button that re-runs `refresh_all()` on demand without a full pipeline run.
5. **`INSPIRATION_MAP.md`** and **`WORKFLOW.md`** — update "How to activate" column from "drop CSV" to "auto-fetched from <source>; manual override still supported by placing your own CSV in `data/`".

## Manual override preserved

If the user drops their own file into `data/`, the fetcher sees it, respects freshness, and only refetches if stale. Broker/paid data (e.g. Screener Pro exports) will always win because the user's file is newer than the auto-fetch cache. No behavior lost.

## Risk & honest caveats

- **NSE/NSDL/Moneycontrol are unofficial scrapes.** They can change HTML any time. That's why every fetcher fails soft: pipeline continues, overlay just runs quiet, user sees a warning in the log. No new hard dependency.
- **yfinance fundamentals coverage for NSE is patchy** (already documented in `fundamental_factor.py`). Auto-fetch doesn't fix that — it just automates what's available. The `fundamentals_overlay` `Fundamentals_Coverage` column already surfaces this per-symbol.
- **Rate limits.** yfinance loop is throttled to `sleep=0.2s`. NSE endpoints use one request per session with a cookie warm-up.
- No new dependencies. No API keys. Fully offline-friendly after first fetch (cache is reused for 24h/7d).

## Files touched

- **New**: `desktop/nse_quant_engine/core/optional_data_fetchers.py`
- **Edit**: `desktop/nse_quant_engine/run_app.py` (Step 0.5 call, enhanced activation checklist, Refresh-now button)
- **Edit**: `desktop/nse_quant_engine/orchestrator.py` (Step 0.5 call)
- **Edit**: `desktop/nse_quant_engine/run_full_workflow.bat` (one extra line before universe build)
- **Edit**: `desktop/nse_quant_engine/INSPIRATION_MAP.md`, `desktop/nse_quant_engine/WORKFLOW.md` (docs)
- **Edit**: `desktop/nse_quant_engine/CHANGES_v4_3.md` (changelog entry)

## Out of scope (kept from prior plan)

- Part 2 (embedded `QWebEngineView` for dashboard HTML) proceeds as previously described in the same round: add PySide6-WebEngine to `setup_windows.bat` / `requirements.txt`, new **Dashboard** tab renders `output/dashboard_latest.html` in-app with a "Open in browser" fallback if WebEngine import fails.
