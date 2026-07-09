# Fix FII/DII fetch by using NSE official endpoints

## Problem
- Moneycontrol path fails because `html5lib` isn't installed in the user's env (they haven't re-run `setup_windows.bat`).
- Groww fallback returns 502.
- Result: `fii_dii_daily.csv` never refreshes.

User's ask: pull FII/DII directly from NSE India (official), which does publish this data for free.

## NSE endpoints we'll use (both free, no login)

1. **Primary — NSE JSON API (today's provisional + previous day final):**
   `https://www.nseindia.com/api/fiidiiTradeReact`
   Returns a small JSON array with entries for `FII/FPI *` and `DII *` including `buyValue`, `sellValue`, `netValue`, `date`, `category`. Requires the same cookie-warmup handshake we already use for the bulk-deals API (hit `nseindia.com` homepage first, then the report page, then the API with `Referer` + `X-Requested-With`). Retry once on 5xx with a 2s backoff.

2. **Secondary — NSE historical archives (fills gaps + backfill):**
   `https://www.nseindia.com/api/historical/fiidiiTradeReact?from=DD-MM-YYYY&to=DD-MM-YYYY`
   Same handshake. Returns up to ~90 days of history in one shot. Used to backfill when the local CSV is missing days.

3. **Tertiary — Moneycontrol** (keep existing, only reachable if `html5lib`/`lxml` present).
4. **Quaternary — Groww JSON** (keep existing).

## Code changes (only `core/optional_data_fetchers.py` + `requirements.txt` housekeeping)

### `core/optional_data_fetchers.py`
- Add helper `_nse_warmup(sess)` that hits homepage → market-data page → FII/DII report page, matching the pattern already in `_bulk_from_nse_api`. Reuse it for both bulk-deals and FII/DII to avoid duplication.
- Add `_fii_dii_from_nse_api(sess)`:
  - warm up cookies
  - GET `/api/fiidiiTradeReact` with proper headers, retry once on 5xx
  - Parse JSON list; group by `date`; sum FII rows (`FII/FPI *`) and DII rows (`DII *`) `netValue` into `FII_Net_INR_Cr` / `DII_Net_INR_Cr`
  - Return normalized DataFrame `Date, FII_Net_INR_Cr, DII_Net_INR_Cr`
- Add `_fii_dii_from_nse_archive(sess, days=90)`:
  - warm up cookies
  - GET `/api/historical/fiidiiTradeReact?from=...&to=...`
  - Same normalization; used to backfill history in one shot
- Update `fetch_fii_dii` source order to:
  1. `nse-api` (today + yesterday, primary)
  2. `nse-archive` (backfill 90 days)
  3. `moneycontrol`
  4. `groww`
  Merge results across whichever sources succeed instead of stopping at the first — `nse-api` only gives 1–2 rows, so we always try `nse-archive` too and union with existing CSV via the existing `_merge_dated` helper. Only fall through to moneycontrol/groww when both NSE calls fail.
- Improve logging so every source attempt prints outcome + row count.

### `requirements.txt`
- Already has `html5lib>=1.1` from the last change; leave as-is. NSE path doesn't need it, so pipeline now works even if the user hasn't re-run `setup_windows.bat`.

## Pipeline usage (verify, no changes expected)
Confirm that `core/institutional_flow.py` (the consumer of `fii_dii_daily.csv`) still reads columns `Date, FII_Net_INR_Cr, DII_Net_INR_Cr` in INR crore — our normalization matches that schema, so downstream scoring/overlays keep working unchanged. If the file reveals a different expectation (e.g., separate buy/sell columns), extend the normalizer to emit those too. No changes to `orchestrator.py`, scoring, or UI.

## Validation
- After edit, ask the user to re-run the pipeline. Expected log:
  ```
  [fetch] fii_dii source 'nse-api' ok (2 rows)
  [fetch] fii_dii source 'nse-archive' ok (60 rows)
  [fetch] fii_dii_daily.csv refreshed via nse-api+nse-archive (~60 rows)
  ```
- If NSE blocks the IP, moneycontrol/groww fallbacks still fire; failure remains soft.

## Out of scope
- No changes to bulk deals, fundamentals, earnings, dashboard, or scoring logic.
