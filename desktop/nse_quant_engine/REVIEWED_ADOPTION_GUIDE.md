# NSE Quant Engine Core v4.1 Reviewed Adoption Guide

This package is Claude's Core v4 with two additional safeguards:

1. **Filtered EV**: expected value must be computed on a relevant slice such as top score bucket / universe group / opportunity type. A generic EV across all historical signals is not candidate-specific enough.
2. **Fundamental coverage guard**: fundamentals only affect stock scores when enough underlying fields are populated. One random PE value should not move ranking.

## Do not use this as a full replacement

Keep your current proven data layer:
- universe_builder.py
- etf_metadata_enricher.py with the import coalescing fix
- etf_aum_auto_fetcher.py
- etf_ter_tracking_auto_fetcher.py
- validation_builder.py
- news_market_builder.py

Add this `core/` folder as a sidecar and integrate in stages.

## Required sequence

1. Apply/keep the metadata coalescing fix first. TER fetched by the TER script must flow into `etf_metadata_enriched.csv`.
2. Add `core/` to your project root.
3. Run: `python tests/test_core.py` from this folder or after copying tests.
4. Wire `validation_status.json` first. Lowest risk, highest reliability gain.
5. Adopt price cache in shadow mode. Compare output against old price build.
6. Run new scoring in parallel for 2-4 weeks: write `latest_scores_v4_shadow.xlsx`, do not overwrite your main ranking immediately.
7. Turn on fundamentals only after checking coverage and values for a few symbols.
8. Use EV only after validation is positive and only for filtered buckets.

## Rule for the dreamy goal

Do not optimize for raw score. Optimize for validated, net, risk-adjusted expected value per holding day.

If validation stays insufficient or negative, the correct action is watchlist-only. The tool doing that is not failing. It is protecting you from a very confident spreadsheet hallucination.
