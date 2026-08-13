# Validation methodology

## The only question that matters

> Does the ranking, applied out of sample and after costs, beat the benchmark?

Everything else in this repository is machinery for answering that honestly.
The most valuable answer this layer can return is frequently:

> **No Proven Edge Yet.**

Believe it when it says so. There is no override.

## Authoritative status file

`output/validation_status.json` is the **single source of truth** for the
verdict. Reports in Markdown are human-readable renderings and must never be
scraped to infer a verdict — that produced false "Validation Positive" results
in earlier versions.

The file records at minimum:

| Field | Meaning |
|---|---|
| `schema_version` | 1 = legacy (`Final_Score` ranking), 2 = current (CAS ranking) |
| `ranking_column` | Active ranking column — currently `Confidence_Adjusted_Score` |
| `verdict` | Plain-language verdict, e.g. `No Proven Edge Yet` |
| `effective_validation_dates` | Dates that actually contributed evidence |
| `information_coefficient` / `_shrunk` | Raw and Bayesian-shrunk IC |
| `hit_rate` / `_shrunk` | Raw and shrunk hit rate |
| `costs_included` | Whether transaction costs are in the excess-return figure |
| `watchlist_only` | Whether the ship gate keeps the system in watchlist mode |

See `examples/sample_output/validation_status.json` for a synthetic example.

## Schema versioning

History accumulated under two regimes:

- **Schema v1** ranked by `Final_Score`.
- **Schema v2** ranks by `Confidence_Adjusted_Score` with `Symbol` ascending as
  tie-breaker.

Migration was controlled, not destructive: v1 rows remain in history but only
**v2 rows carry verdict authority**. Mixing them would silently evaluate two
different rankings as one track record. Never delete historical rows to "clean
up" a chart.

## Effective dates, not calendar dates

A verdict requires a minimum number of *effective* validation dates — dates
where forward returns and scores both exist for enough names to compute a
meaningful cross-sectional statistic. A long calendar span with sparse coverage
does not qualify.

## Bayesian shrinkage

Raw IC and hit rate on a short history are dominated by noise. Both are shrunk
toward a null prior; the shrunk values are what the verdict uses. A shrunk IC
near zero with a wide interval is reported as no edge, not as a small edge.

## Walk-forward and survivorship

Alphas are evaluated walk-forward: parameters and weights available at time *t*
are used only to predict returns after *t*. Candidate alphas additionally face
an **incremental (residual) IC gate** — IC is measured on the residual after
regressing out existing survivors, so a new alpha must add information rather
than restate momentum in different units.

## Costs

Excess return is reported **after** modelled transaction costs (brokerage,
impact, spread assumptions in `core/config.py`) and turnover. Turnover-aware
alpha weighting exists so a signal is not rewarded for churn it cannot pay for.
Expected value per holding day stays blank until validation is positive.

## Ship gate

Portfolio selection and the trade plan consult the validation status. Without a
positive verdict the system remains **watchlist-only**: it will show candidates
and reasoning, but will not present them as actionable positions.

The gate is **fail-closed** (`core/portfolio_validation.py`). Missing evidence is
never treated as passing evidence. A batch is downgraded to
`Downgrade_To_Watch` whenever any of the following holds, however clean the
metrics that *could* be computed happen to look:

- `validation_status.json` verdict is not `Validation Positive`
- the official Top-5 (`trade_plan_latest.csv`) is missing or unreadable
- any critical `top5_*` artifact is absent or carries no symbols
  (`artifact_completeness = false`, offenders listed in `missing_artifacts`)
- any artifact's symbol set differs from the official Top-5
  (`symbol_set_aligned = false`)
- any artifact's symbol **order** differs from the official Top-5
  (`symbol_order_aligned = false`) — a positional join would otherwise pair the
  wrong numbers to the wrong symbol

Only complete *and* consistent evidence can reach `Ship`. Expected value is held
to the same standard: `core/expected_value.py` refuses to compute when a
requested filter column is absent from forward-return history, rather than
publishing an unfiltered statistic under a filtered label.

## What this does not prove

- It does not prove future performance.
- It does not account for your liquidity, slippage, taxes or broker.
- It cannot detect a regime break before it happens.
- A positive verdict on a short history is still a weak claim.

This software is research tooling and provides **no investment advice**.
