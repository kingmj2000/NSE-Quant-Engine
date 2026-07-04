# NSE Quant Engine Stage 3.3 Final Evidence Pack - Workflow

## Purpose

This is a personal screening and validation system for:

- Nifty 50
- Nifty Next 50
- NSE-listed ETFs

It is designed to narrow the universe into review candidates and then test whether the scoring method has actually shown evidence of working after costs. It does not guarantee low-risk high-return trades, because that is not how markets work, despite humanity's ongoing attempts to negotiate with probability.

## Full weekly workflow

```bat
python universe_builder.py
python etf_quality_builder.py
python nse_quant_engine.py
python validation_builder.py
python cross_sectional_validation.py
python news_market_builder.py
```

Or run:

```bat
run_full_workflow.bat
```

## Daily workflow

Once `config.csv` and ETF quality files already exist:

```bat
python nse_quant_engine.py
python validation_builder.py
python cross_sectional_validation.py
python news_market_builder.py
```

## Main outputs

```text
output/latest_scores.xlsx
output/latest_scores_validated.xlsx
output/weekly_report.md
output/cross_sectional_validation_report.md
output/news_market_context.md
output/score_history.csv
output/signal_history.csv
output/forward_return_history.csv
output/forward_return_missing_signals.csv
output/score_bucket_performance.csv
output/cross_sectional_spread_by_date.csv
output/cross_sectional_spread_summary.csv
```

## Review order

1. `output/cross_sectional_validation_report.md`
2. `output/latest_scores_validated.xlsx`
3. `output/news_market_context.md`
4. `output/forward_return_missing_signals.csv`
5. `output/score_bucket_performance.csv`

## Validation verdict logic

For `Validation Positive`, the selected horizon must pass:

```text
Validation_Dates >= CrossVal_Min_Dates
Effective_Validation_Dates >= CrossVal_Min_Effective_Dates
Avg_Obs_All >= CrossVal_Min_Obs
Avg_TopMinusBottom_Quintile >= CrossVal_Min_Spread
Hit_Rate_TopBeatsBottom >= CrossVal_Min_HitRate
Adjusted_TStat_TopMinusBottom >= CrossVal_Min_TStat
Bootstrap_Prob_Positive >= CrossVal_Min_Bootstrap_Prob
```

Defaults are stored in `scoring_rules.csv`.

## Early-run behavior

At first, expect:

```text
Insufficient History
```

That is correct. The system needs enough completed 5D/10D/21D forward windows before the validation can mean anything.

## AI review workflow

Upload these to Claude Project or ChatGPT:

```text
output/latest_scores_validated.xlsx
output/cross_sectional_validation_report.md
output/news_market_context.md
output/weekly_report.md
output/score_history.csv
output/signal_history.csv
output/forward_return_history.csv
output/forward_return_missing_signals.csv
```

Use `report_agent_prompt.md`.

The AI should not compute new scores. It should:

- Check validation verdict first
- Review top candidates
- Check whether news contradicts the quant result
- Downgrade candidates with weak validation, weak ETF quality, liquidity risks, or negative news
- Produce a final personal review shortlist

## Practical rule

If validation says:

```text
Insufficient History
No Proven Edge Yet
Validation Negative
```

then treat the output as a watchlist only.

If validation says:

```text
Validation Positive
```

then still review candidate-level risk, news, liquidity, and ETF quality before doing anything with real money.
