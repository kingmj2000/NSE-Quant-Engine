
## Step 2 — Correlation-aware top-5 + Benchmark/IR panel

Two additive changes that sharpen the "low-risk" and "beats the index" claims for the final top-5. No pipeline stages are re-ordered, no new pip deps.

### A. Correlation-aware top-5 selection

**Problem today.** `trade_plan_builder.py` picks the top 5 by `Final_Score` alone, so five IT stocks can (and do) end up together — that's concentrated single-factor risk, not diversified low-risk.

**Approach.** Greedy diversification from a wider top-N pool:
1. Take top-N (default 25) candidates by `Final_Score` after governance veto.
2. Compute the 60-session daily-return correlation matrix from cached prices (reuse `core/price_cache.py`; benchmark and price columns already loaded by `nse_quant_engine.py`).
3. Pick the highest-scoring candidate as seed. Iteratively add the candidate that maximises `α·norm_score − (1−α)·max_corr_with_selected`, where `norm_score = Final_Score/100` and `α = CORR_AWARE_ALPHA` (default 0.65). Stop at 5.
4. If correlations can't be computed (short history, missing prices), silently fall back to today's ranking.

**New module:** `core/portfolio_selection.py`
- `pairwise_corr_60d(prices_long, symbols) -> pd.DataFrame`
- `diversified_top_n(candidates_df, corr, n=5, alpha=0.65) -> list[str]`
- Pure, unit-testable.

**Wiring:** `trade_plan_builder.py` — after ranking, call `diversified_top_n`; write `output/top5_corr_matrix.csv` for the dashboard.

**Config knobs (`core/config.py`):**
```
CORR_AWARE_TOP5    = True     # feature flag
CORR_AWARE_POOL_N  = 25
CORR_AWARE_ALPHA   = 0.65     # score vs diversification tradeoff
CORR_WINDOW_DAYS   = 60
```

### B. Benchmark / Information-Ratio panel on the dashboard

**Per candidate compute** (in `trade_plan_builder.py`, written to `output/top5_benchmark_stats.csv`):
- `Excess_21D`  = stock 21D return − Nifty50 21D return
- `TrackingError_63D` = std of daily excess return over 63 sessions × √252
- `InformationRatio_63D` = mean(daily excess) / std(daily excess) × √252
- `BetaVsNifty_63D` = cov(stock, nifty)/var(nifty), 63D window

**Dashboard render (`dashboard_html_builder.py`):**
- New "Alpha vs Nifty 50" strip inside each top-5 card: 4 small metrics (Excess 21D, IR 63D, TE 63D, β).
- New "Top-5 correlation matrix" tile placed in the two-column grid next to the existing charts. Rendered as a plain HTML table with green→red cell shading (no new chart lib — cell background tinted by `hsl()` on the client). Includes an "avg |corr|" badge so the user sees at a glance whether the 5 are diversified.

### Files touched

```text
core/portfolio_selection.py       (NEW, pure)
core/config.py                    (+4 flags)
trade_plan_builder.py             (call selector; emit 2 CSVs)
dashboard_html_builder.py         (load 2 CSVs; render per-card strip + matrix tile)
tests/test_new_modules.py         (+2 tests: diversification picks less-correlated names;
                                    IR math correct on synthetic returns)
```

### Guardrails

- Feature-flagged (`CORR_AWARE_TOP5=True` default, but any exception → silent fallback to score-only ranking so the pipeline can't regress).
- Benchmark stats compute inside a `try/except`; missing benchmark file → panel simply hidden, not crashed.
- No changes to `scoring.py`, `orchestrator.py` step order, or `run_app.py` — same crash-isolation contract as before.
- Alpha Zoo from step 1 stays untouched (still not wired into scoring; that's a later step gated on validation).

### Validation before I hand off

- `python tests/test_new_modules.py` — all existing tests + 2 new tests pass.
- Synthetic dry-run of `portfolio_selection.diversified_top_n` in a scratch script (5 correlated + 5 uncorrelated names → chosen 5 include at least 3 uncorrelated ones).
- `py_compile` on the two modified modules.
- Dashboard HTML: grep the emitted file for the new tile id and the per-card strip.

## Confirm to proceed

Green-light Step 2 as scoped, or trim (e.g. selection-only, defer benchmark panel)?
