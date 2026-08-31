# Source-policy honesty pass — FII/DII, bulk deals, fundamentals cache

Context-only change. No scoring, ranking, validation, portfolio, shadow or adaptive code is touched.

## Verified before planning

- `fetch_fii_dii` (core/optional_data_fetchers.py) currently calls four sources every run — `nse-api`, `nse-archive` (2 attempts + sleeps), `moneycontrol`, `groww` — with no memory of prior failures.
- **`fetch_fundamentals` overwrites.** It builds `out` from this run's yfinance result only and calls `out.to_csv(target, index=False)`. There is no merge with the existing `fundamentals_latest.csv`, which fully explains the 32→104 swing.
- `_report_flow_coverage()` is shared by fii_dii and bulk_deals and uses a pure business-day calendar, so it cannot distinguish "never fetched" from "no deals that day".
- There is no `missing_date_ranges()` helper, no `core/safe_io.py` and no `data/source_health.json` in the repo today; all are new. `missing_date_ranges()` also exists in a pending patch, so it is implemented here in `core/optional_data_fetchers.py` with exactly that name and signature `(target: Path, days: int)` so the two converge instead of conflicting.
- `etf_metadata_enricher._write_nav_cache_if_better()` does not exist under that name — the merge rule will be written fresh, following the described semantics.
- Institutional-flow UI lives in the Context tab's `MacroRotationView` (run_app.py ~line 1295–1330).

## 1. Source policy

New in `core/optional_data_fetchers.py`:

- `KNOWN_UNAVAILABLE_SOURCES = {"moneycontrol", "groww"}` with per-source measured reason and date (2026-08-31 probe: Moneycontrol JS-rendered, zero `<table>` tags in 105 KB; Groww 502/empty). Adapters stay in the file, unused. `NSE_TRY_ALL_SOURCES=1` re-enables them.
- One line per run: `[fetch] fii_dii: moneycontrol, groww skipped — known unavailable (see KNOWN_UNAVAILABLE_SOURCES)`.
- `data/source_health.json`: per-source last-attempt date + outcome. `nse-archive` is attempted at most once per calendar day; otherwise `[fetch][try] fii_dii: 'nse-archive' skipped — blocked earlier today (503 block page)`. It stays in the chain so a lifted block is picked up next day.
- Comment block records why each source was retired and on what evidence.

## 2. Optional `nselib` adapter

- Import inside the adapter, `try/except ImportError`; never a hard dependency. `requirements.txt` gains an OPTIONAL section stating the engine runs fine without it.
- `_resolve_nselib_fn(candidates)` searches `nselib.capital_market`, `nselib.derivatives`, then top-level `nselib`, matching case- and underscore-insensitively; logs the name it bound. On no match it raises with both the attempted candidates and the module's `dir()`.
- `_fii_dii_from_nselib(sess)` appended after `nse-archive`; emits exactly `Date, FII_Net_INR_Cr, DII_Net_INR_Cr` (dates `YYYY-MM-DD`, values numeric) via `_normalize_nse_fiidii_rows`. Signature is inspected: a range-capable function is driven by a new `missing_date_ranges(target, days)` helper; a latest-day-only function logs `nselib fii_dii: latest-day only, cannot backfill`. Empty frame raises.
- `_bulk_from_nselib(sess, days)` appended after `bse`, same columns as `_bulk_from_nse_archive`.
- No nselib fundamentals or earnings adapter.
- `_describe_source_error()` gains a `library` class: `ImportError` → `nselib not installed for this interpreter — run: python -m pip install nselib`; `AttributeError` → `nselib API changed — see the resolver log for available names`. On first nselib use the log records the nselib version and `sys.version` / `sys.executable`, because the target machine has both Python 3.10 and 3.13 and a package visible to only one has already produced two false diagnoses.

## 2b. Fundamentals cache merge

Consumer grep (amendment 2) — every reader accesses columns **by name**, none positionally, none asserting an exact column set:
- `nse_quant_engine_v4_shadow.py:167-190` selects `["Symbol","Fundamental_Score","Fundamental_Coverage"]` if present.
- `core/fundamentals_overlay.py` reads named fields, NaN-safe.
- `run_app.py:554`, `ui/decision_center.py:494` only list the path for a health/presence display.
- `tests/test_optional_data_fetchers.py:184` asserts `Symbol` and `Fundamental_Score` exist and NaN-ness — no exact-set assertion.
Adding `As_Of` and `Fundamentals_Stale_Days` is therefore safe.

`fetch_fundamentals` becomes merge-on-`Symbol`: a freshly fetched non-null value wins; a symbol that returned nothing keeps its cached value. Adds `As_Of` (when the value was actually fetched) and `Fundamentals_Stale_Days`. Logs `fundamentals: N fetched, M reused from cache, K never populated`. The scored count can then only climb.

**Guarded cache read.** The existing `fundamentals_latest.csv` is read through a local guard (no `core/safe_io.py` exists): missing file, zero-byte file, or any parse failure — including `pandas.errors.EmptyDataError` — is treated as "no cache", logged once, and the run proceeds with fresh data only. `pd.read_csv` is never allowed to raise out of this path; that failure mode has already halted the pipeline once.

**Pruning is against the FULL universe.** Stale rows are pruned against the complete `config.csv` symbol list, never against today's yfinance fetch shortlist (capped at 120 of ~597 universe rows). Pruning to the shortlist would delete cached fundamentals for every symbol outside today's cap — destroying exactly what the cache exists to preserve — while still looking correct, because the scored count is measured inside the 120. Only symbols absent from the universe entirely are dropped.

## 2c. Bulk-deals gap report

`_report_flow_coverage` gets per-feed handling. For `bulk_deals` the generic business-day warning is suppressed, because the archive source covers a single day and only accumulates on days a fetch succeeded — absence is ambiguous by construction. It is replaced with a statement derived from `source_health.json` fetch-success dates: days fetched successfully with zero rows are reported as genuine no-deal days, days never fetched as unfetched. Any bulk-deals date **earlier than the first entry in `source_health.json`** is reported as `unknown — no fetch record`; it is never labelled a genuine no-deal day, since no record exists to support that claim. The decision is pinned by a test.


## 2d. Earnings calendar

Untouched.

## 3. Late-run warning

After a successful `fetch_fii_dii`, compare the newest row's date to the last completed business day; when older, print the four-line notice telling the user to run after 19:00 IST because a missed day cannot be recovered.

## 4. UI caption

In the Context tab's institutional-flow section, when coverage gaps exist show `Incomplete series — N of M business days on record. Gaps are missing data, not zero flow.` No hiding, no interpolation.

## 5. Tests — `tests/test_source_policy.py` (no network, monkeypatched)

`test_known_unavailable_not_called_by_default`, `test_known_unavailable_called_with_env_override`, `test_nse_archive_attempted_once_per_day`, `test_nse_archive_retried_next_day`, `test_nselib_import_error_continues_chain_as_try`, `test_nselib_import_error_classified_as_library`, `test_nselib_attribute_error_classified_as_library`, `test_resolver_matches_case_and_underscore_variants`, `test_resolver_failure_lists_available_names`, `test_nselib_adapter_schema_matches`, `test_nselib_empty_frame_raises`, `test_stale_day_warning_fires`, `test_stale_day_warning_silent_when_current`, `test_ui_caption_only_when_gaps`, `test_fundamentals_failed_symbol_keeps_cached_value`, `test_fundamentals_success_updates_value`, `test_fundamentals_scored_count_never_falls`, `test_fundamentals_stale_days_reflects_reuse`, `test_fundamentals_retains_universe_symbol_outside_todays_shortlist`, `test_fundamentals_drops_symbol_removed_from_universe`, `test_fundamentals_cache_read_survives_empty_file`, `test_fundamentals_cache_read_survives_corrupt_file`, `test_bulk_deals_gap_policy`, `test_bulk_deals_dates_before_first_record_are_unknown`.

Then the full suite is run and the exact pass count reported.
