
# Fix silent feed failures, expose data health, repair shadow inputs

Priority-ordered. Parts 1–3 are wiring/data fixes; Part 4 is a small UI addition. No LLM. All new UI reads existing output files. Missing data → "not yet available"/RED, never fabricated.

## PART 1 — Fix broken fetchers (`core/optional_data_fetchers.py`)

### 1a. `fetch_delivery_pct` — new NSE bhavdata URL + resilient header detection

- Replace `_bhavcopy_url` to use current archive path:
  `https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv`
  Keep the old `archives.nseindia.com` URL as fallback (try new first, then old).
- Warm cookies via `_nse_warmup(sess)` before each day's GET; send full browser headers (UA, Accept, Accept-Language, Referer=`https://www.nseindia.com/`, `Sec-Fetch-*`).
- Robust header detection: strip whitespace from every column name (existing code does this but the real file has leading spaces in headers like ` SERIES`, ` DATE1`, ` DELIV_PER`). Rewrite the column picker to:
  - Normalize each header via `re.sub(r"[^a-z0-9]", "", c.lower())`.
  - Map: symbol→`symbol`, series→`series`, date→`date1` or `date`, deliv%→any of `delivper`, `dlyqttotradedqty`, `delivqty` (percentage variant preferred).
  - If deliv% column missing, look for both `deliv_qty` and `ttl_trd_qnty` and compute `100 * deliv/ttl` as fallback.
- Filter series to `{EQ, BE, ""}` as today.
- On success: print `"delivery% for YYYY-MM-DD: N symbols"` (already done).
- Append-only cache preserved.

### 1b. `fetch_iv_rank` — browser session, backoff, polite pacing

- Introduce `_nse_browser_session()` returning a `requests.Session` with full browser headers (`User-Agent`, `Accept`, `Accept-Language: en-IN,en;q=0.9`, `Accept-Encoding: gzip, deflate, br`, `Connection: keep-alive`, `Upgrade-Insecure-Requests: 1`).
- Warmup sequence before any option-chain call: GET `https://www.nseindia.com/` → GET `https://www.nseindia.com/option-chain` → sleep 1s. Reuse the same session across all symbols.
- Per-symbol request: set `Referer=https://www.nseindia.com/option-chain`, `Sec-Fetch-Site=same-origin`, `Sec-Fetch-Mode=cors`, `Sec-Fetch-Dest=empty`. On 401/403/429 or empty payload, exponential backoff (1s → 3s → 7s), max 3 attempts; if still blocked, re-run warmup once and retry once more.
- Polite pacing: `time.sleep(0.6)` between symbols (up from 0.2).
- Preserve cache-append-only semantics (unchanged block).

### 1c. Cache-append-safety audit (both fetchers)

Confirm and document: on any failure path, both functions `return target.exists()` without writing. Existing code already does this — add a comment `# NEVER overwrite good cache on failure` at each write site, and add a `[fetch] reused cached delivery_pct (N rows, last=YYYY-MM-DD)` log when a fresh fetch fails but cache exists.

### 1d. Write `data/data_health.json`

Add `_write_health_row(feed, status, rows, last_date, note)` and call it at the end of every fetcher (fii_dii, bulk_deals, fundamentals, earnings, delivery_pct, iv_rank). Also seed rows for `price` (from `data/price_cache_meta.json` or newest file in `data/prices/`) and `amfi_nav`/`amfi_aum`/`amfi_ter` (from existing standardized CSVs' mtime) and `news` (from `output/news_market_context.md` mtime) inside `refresh_all`. Schema:

```
{
  "generated_at": "2026-07-09T14:22:00",
  "feeds": {
    "delivery_pct":  {"status": "green|amber|red", "rows": 4823, "last_date": "2026-07-08", "note": "..."},
    "iv_rank":       {"status": "red", "rows": 0,   "last_date": null,        "note": "60 misses — NSE blocked"},
    ...
  }
}
```

Status rules: green = fetched today/yesterday; amber = 2–7 days stale OR yfinance returned empty for NSE tickers; red = >7 days stale or never fetched.

## PART 2 — "Data health" panel + inactive-signal note (`dashboard_html_builder.py`)

### 2a. Data health panel

New glass card, injected between the "Progress to a verdict" section and "Market context". Reads `data/data_health.json`. Renders a compact table: feed name • chip (●green/●amber/●red styled with existing tokens) • last successful date • rows fetched • one-line note. If `data_health.json` is missing, panel shows a single amber row "Data health snapshot not yet available."

### 2b. Plain-English inactivity note

In `_plain_summary_html`, after the main sentence(s), append one extra sentence when any RED/AMBER feed is one the score currently uses. Score-consuming feeds map: `delivery_pct → delivery_momentum`, `iv_rank → iv_rank`, `fii_dii → institutional_flow`, `fundamentals → fundamental_factor`, `bulk_deals → institutional_flow`, `earnings → event_calendar`. Sentence template:

> "Note: {feeds_pretty} data feeds are not updating right now, so those signals are currently inactive."

Deterministic; drops when no relevant RED/AMBER feeds present.

## PART 3 — Shadow wiring fixes (`nse_quant_engine.py` write path)

Investigate `nse_quant_engine.py` to confirm `latest_scores.csv` columns, then:

- Ensure the following columns are always written to `output/latest_scores.csv`: `Price`, `MA_20`, `MA_50`, `MA_200`, `Trend_Rank`, `Return_21D`, `Benchmark_Return_21D`, `Rel_Strength_21D`. These are the inputs `nse_quant_engine_v4_shadow.py` needs; missing them is why shadow neutralizes trend + RS.
- Ensure `data/fundamentals_latest.csv` is written by `fetch_fundamentals` with header `Symbol,Fundamental_Score,...` (currently the file lacks `Symbol` and `Fundamental_Score` — likely writing raw yfinance rows). Compute `Fundamental_Score` inside `core/fundamental_factor.py`'s existing scorer during the fetch step (or a thin wrapper), and write `Symbol` + `Fundamental_Score` + the raw component columns.
- If yfinance returns empty for NSE tickers, still write the file with `Symbol` populated from the shortlist and `Fundamental_Score=NaN`, and mark `fundamentals` as AMBER in `data_health.json` with note "yfinance empty for NSE tickers".

## PART 4 — Light UI refinement

- Data Health panel exposes fundamentals AMBER state per Part 3.
- No new tabs beyond Data health.
- No visual changes to candidate cards while verdict = Insufficient History.

## Verification (must show in final output)

1. Run `python -c "from core.optional_data_fetchers import fetch_delivery_pct, fetch_iv_rank; from pathlib import Path; fetch_delivery_pct(Path('data')); fetch_iv_rank(Path('data'), Path('.'))"` on the dev machine and paste the `[fetch] delivery%…` and `[fetch] iv_rank_daily.csv refreshed (hit=… miss=…)` log lines showing non-zero row counts for a real recent trading day. If the sandbox cannot reach `nseindia.com`, run against a saved fixture CSV under `tests/fixtures/sec_bhavdata_full_sample.csv` and note it explicitly.
2. Show `data/data_health.json` contents after the run.
3. Render `dashboard_latest.html`, screenshot the new Data health panel and confirm the plain-English inactivity note appears iff a used feed is RED/AMBER.
4. Run the shadow pipeline; confirm `latest_scores.csv` has the 8 new columns and `fundamentals_latest.csv` has `Symbol,Fundamental_Score`. Confirm shadow log no longer says "neutralized trend/RS" or "0 fundamentals scored".

## Tests to add/extend

- `tests/test_optional_data_fetchers.py` (new or extended):
  - `test_delivery_pct_header_detection_current_format` — fixture CSV with `SYMBOL, SERIES, DATE1, DELIV_PER` headers (leading spaces).
  - `test_delivery_pct_header_detection_legacy_format` — fixture with `%DlyQtToTradedQty`.
  - `test_delivery_pct_missing_percent_falls_back_to_ratio` — fixture with only `DELIV_QTY, TTL_TRD_QNTY`.
  - `test_iv_rank_session_warmup_called` — monkeypatch `requests.Session.get` to record URL order; asserts homepage + option-chain page hit before any `api/option-chain-equities` call.
  - `test_cache_not_wiped_on_failure` — pre-seed cache, make fetcher raise, assert file unchanged.
- `tests/test_shadow_wiring.py` (new):
  - After a mocked `nse_quant_engine` run, assert `latest_scores.csv` contains the 8 required columns and `fundamentals_latest.csv` has `Symbol, Fundamental_Score`.

## Guardrails (unchanged)

Glassmorphic theme, honesty captions, watchlist-only framing, dormant adaptive layer, "cross-sectional report is the authority" wording, plain-English disclaimer panel — all preserved. No candidate card looks more actionable. No new computation in the dashboard layer.
