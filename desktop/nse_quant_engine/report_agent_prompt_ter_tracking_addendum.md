# ETF TER / Tracking Review Addendum

Use these additional files when reviewing ETF candidates:

- data/etf_metadata_imports/auto_amfi_ter_tracking_latest.csv
- data/amfi_ter_tracking_source_standardized.csv
- data/etf_ter_tracking_match_diagnostics.csv
- data/etf_ter_tracking_auto_fetch_log.csv
- data/etf_ter_tracking_auto_debug_report.md

Rules:

1. Treat TER as valid only when present as a decimal value that passes sanity checks.
2. Treat tracking error as valid only when present and non-negative.
3. Tracking difference is useful but secondary; missing tracking difference should not override TER + tracking error if those two are present.
4. If TER and Tracking_Error are still missing after the fetcher ran, say "TER/tracking source did not match or did not return ETF rows" rather than implying the scoring engine failed.
5. Do not invent TER, tracking error, tracking difference, or benchmark index.
6. ETF candidates with NAV + AUM + TER + Tracking_Error populated can be treated as materially better-quality ETF candidates than ETFs missing those fields, subject to liquidity and validation status.
7. If validation verdict is not exactly "Validation Positive", all trade plans remain watchlist-only.
