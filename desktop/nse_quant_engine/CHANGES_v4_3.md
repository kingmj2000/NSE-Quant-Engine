# Changelog — Steps 6–16 (v4.2 → v4.3)

Added between the last release and now. All changes are 100% local, no new
pip deps, no runtime AI, no API keys.

## Step 0.5 — Optional-data auto-fetchers (new)
- `core/optional_data_fetchers.py`: pulls `fii_dii_daily.csv` (Moneycontrol),
  `bulk_deals.csv` (NSE JSON), `fundamentals_latest.csv` (yfinance), and
  `earnings_calendar.csv` (yfinance) at the start of every run.
- Fails soft — a source outage never breaks the pipeline; last-good cache is
  kept and the run continues with the overlay quiet.
- Freshness cache: 24h for flow feeds, 7d for fundamentals/earnings. User-
  dropped CSVs always win when newer.
- Wired into `orchestrator.py` (Step 0.5 before universe_builder) and
  `run_full_workflow.bat`; UI adds a **🔄 Refresh optional feeds now** button
  and shows per-file freshness + row count on the dashboard.


## Step 6 — Fundamentals & Quality Overlay
- `core/fundamentals_overlay.py`: z-score quality blend + Cheap/Fair/Expensive vs 3Y self-median + sector-median PE.
- Report-only (`QUALITY_WEIGHT=0.0`) until IC review.

## Step 7 — Evidence Bundle (AI handoff)
- `core/evidence_bundle.py`: zips all CSVs/JSONs + `README_for_AI.md` + `evidence.json` into `output/insight_bundle_<ts>.zip`.
- `prompts/rationale_prompt.md`: strict-JSON output contract for the external LLM.

## Step 8 — Position Sizing
- `core/position_sizer.py`: `risk_parity_lite` (Newton solve, numpy) + `inverse_vol` modes; per-pick `Weight_%`, `Capital_INR`, `Stop_Loss_INR`, `Max_Loss_%_of_NAV`.

## Step 9 — Walk-Forward Backtest
- `core/backtest_engine.py`: strict no-lookahead style backtest; hit rate, Sharpe, Sortino, drawdown for Top-5 EW / Top-1 / Benchmark variants.

## Step 10 — Sector & Peer Context
- Extended `core/sector_context.py` with `enrich()`: sector RS 21D/63D vs Nifty + 3 nearest-correlated peers + peer median 3M return.

## Step 11 — Event & Catalyst Calendar
- `core/event_calendar.py`: `Event_Risk_Flag` (In_Window / Pre_Earnings / Post_Earnings / Clear / Unknown) vs recommended hold horizon; uses optional fundamentals-cache columns `NextEarningsDate` / `ExDividendDate`.

## Step 12 — Expected Value & Kelly-Lite Cross-Check
- Extended `core/expected_value.py` with `top5_ev_report()`: per-pick EV_%, Kelly_Fraction_Capped, `EV_Sizing_Agree` flag.
- Report-only unless `KELLY_OVERRIDE=1`.

## Step 13 — Portfolio-Level Validation Gate
- `core/portfolio_validation.py`: single go/no-go on avg |corr|, sum of Max_Loss_%_of_NAV, sector concentration, backtest hit rate, alpha survivors, macro regime, event risk. Emits `Batch_Verdict` ∈ {Ship, Ship_With_Caveats, Downgrade_To_Watch}.

## Step 14 — Institutional-Flow Overlay
- `core/institutional_flow.py`: reads optional `data/fii_dii_daily.csv` and `data/bulk_deals.csv`; produces `FII_Regime`, `Bulk_Deal_Flag`, `Institutional_Confirmation`. Silently degrades if files are missing.

## Step 15 — Regime-Conditional Alpha Tilt
- `core/regime_tilt.py`: reweights alpha-zoo survivors by macro regime (RISK_OFF → low_vol + mean_reversion up; RISK_ON → momentum up).
- Report-only (`REGIME_TILT_APPLY=0`) — writes `regime_tilt_report.json` for the LLM handoff.

## Step 16 — Rebalance / Turnover Diff
- `core/rebalance_diff.py`: compares today's top-5 vs `output/history/top5_prev.csv`; emits holds / exits / entries / turnover_% / net_edge_after_cost_% / recommendation (Hold / Rotate / Manual_Review).

## Cross-cutting
- `core/config.py`: new knobs for every step; `INSIGHT_SAFE_MODE=1` disables Steps 3–16 in one shot.
- `trade_plan_builder.py`: all steps wired under guarded try/except blocks.
- `core/evidence_bundle.py`: all new artifacts included; per-pick `evidence.json` records extended with `sector_context`, `event_calendar`, `expected_value`, `institutional_flow`; top-level records extended with `portfolio_validation`, `regime_tilt`, `rebalance_diff`.
- `prompts/rationale_prompt.md`: file table + JSON contract updated to require commentary on sector context, event risk, EV sanity, institutional flow, batch verdict, regime tilt agreement, rotate-vs-hold.
- `tests/test_new_modules.py`: ~14 new tests covering Steps 6–16.

## Explicitly out of scope
Not built (either duplicates existing engine functionality, or is outside the
cash-market NSE brief the user set):
- Options / F&O overlay.
- Live paper-trading / broker integration.
- Multi-agent LLM debate at runtime (external Claude handoff covers analysis).
- Terminal UI beyond the existing HTML dashboard.
- Alt-data (satellite, cards) — not freely available for NSE.

## Fetcher hardening (post-review)
- `optional_data_fetchers.fetch_fii_dii`: now tries `moneycontrol` (with lxml → html5lib → bs4 parser fallback) then `groww` public JSON; added `html5lib` to `requirements.txt` so `pandas.read_html` never fails on missing optional parser.
- `optional_data_fetchers.fetch_bulk_deals`: replaced single NSE JSON call with `nse-archives` (static daily CSV, no cookie handshake) → `nse-api` (with stronger cookie chain + retry on 5xx) → `bse` JSON fallback for cross-exchange coverage.
- Per-source failures now log `bulk_deals source 'X' failed: <err>` so the operator sees which fallback triggered.


## v4.4 — Signal upgrades (Part A) + gated adaptive weighting (Part B, dormant)

Follows the "tightened plan" the user approved: signal-quality upgrades ship
live; adaptive weighting ships dormant with six mandatory guardrails until
the shadow-vs-primary report justifies manual promotion.

### Part A — Signal upgrades (LIVE)
- **A2 · Sector-neutral scoring** — new `core/sector_neutralize.py`.
  Per-sector z-score for sectors ≥ `SECTOR_NEUTRAL_MIN_MEMBERS` (default 5);
  smaller sectors are left universe-standardized and logged as `Skipped`
  in `output/scoring_sector_neutralization.csv`.
- **A3 · Turnover-aware alpha weights** — new `core/alpha_weighting.py`.
  `w_i ∝ IC_i / (1 + λ · turnover_i)`, `λ = TURNOVER_LAMBDA`. IC input is
  **walk-forward survivor IC** from `alpha_evaluator` (matured windows only)
  — never in-sample. Non-survivor alphas stay at baseline weight.
  Artifact: `output/alpha_weights_current.json`.
- **A4 · Delivery % candidate alpha** — `fetch_delivery_pct()` pulls NSE
  bhavcopy `%DlyQtToTradedQty` per trading day; **append-only cache** with
  backoff, dedupe on `(Date, Symbol)`, and **fail-soft** (a bad fetch never
  wipes existing rows or blocks the pipeline). New `delivery_momentum`
  alpha registered in `alpha_zoo`, gated by the evaluator + residual-IC.
- **A5 · IV rank candidate alpha** — `fetch_iv_rank()` reads NSE
  `option-chain-equities`, computes ATM IV percentile vs trailing 252
  cached days. Same append + backoff + fail-soft discipline. New
  `iv_rank` alpha registered in `alpha_zoo`.
- **A6 · Incremental (residual) IC gate** — `alpha_evaluator.residual_ic()`
  and `build_promotion_log()`. A candidate passes only if standalone IC/
  t-stat clear the existing gates AND its **residual** IC (after regressing
  out current survivors, with an intercept term) clears
  `ALPHA_INCREMENTAL_IC_MIN`. Both standalone and residual IC are recorded
  in `output/alpha_promotion_log.json`.

### Validation-layer Bayesian shrinkage (always on)
- New helpers in `core/validation_status.py`:
  `shrink_hit_rate` (Beta prior toward 0.5), `shrink_ic` (toward 0), and
  `apply_bayes_shrink` used by `cross_sectional_validation.py` before the
  ship/hold gate. Raw values kept as `hit_rate_raw` / `spread_raw` /
  `adj_tstat_raw` in `validation_status.json` for transparency.
  Controlled by `VALIDATION_BAYES_SHRINK` / `VALIDATION_HITRATE_PRIOR_*`
  / `VALIDATION_IC_PRIOR_N`.

### Part B — Adaptive alpha weighting (DORMANT / shadow-only)
- New `core/adaptive_weights.py` with six enforced guardrails:
  (1) walk-forward only — refuses look-ahead rows;
  (2) validation-gated — requires `verdict == "Validation Positive"` AND
      **effective (overlap-adjusted) dates ≥ `ADAPTIVE_MIN_DATES` (default 60)**;
  (3) shadow-first — writes only to `adaptive_weights_shadow.json` /
      `adaptive_weights_log.json`, never the primary weight file;
  (4) heavily regularized — shrinkage-alpha blend, per-weight `MAX_STEP`
      cap, AND a new **total-drift cap `ADAPTIVE_MAX_TOTAL_DRIFT` (default 0.30)**;
  (5) refuses per-symbol keyed inputs (asserts `Symbol`-like columns absent);
  (6) `ADAPTIVE_ENABLED = False` by default — one env flag makes it inert.
- Hook in `nse_quant_engine_v4_shadow.py::_run_adaptive_shadow()` runs every
  shadow build; when dormant, log records the exact reason
  (e.g. `"insufficient effective history (N_eff=…, need 60)"`).

### Evidence bundle
- `core/evidence_bundle.py` now also includes:
  `alpha_promotion_log.json`, `alpha_weights_current.json`,
  `scoring_sector_neutralization.csv`, `adaptive_weights_shadow.json`,
  `adaptive_weights_log.json`.

### Data / output additions
- Data (fetched, cached, append-only):
  `data/delivery_pct_daily.csv`, `data/iv_rank_daily.csv`.
- Output: as listed above.

### Config keys added
```
SECTOR_NEUTRAL, SECTOR_NEUTRAL_MIN_MEMBERS, TURNOVER_LAMBDA,
ALPHA_INCREMENTAL_IC_MIN,
ADAPTIVE_ENABLED (default False), ADAPTIVE_MIN_DATES (60, effective),
ADAPTIVE_SHRINKAGE_ALPHA, ADAPTIVE_MAX_STEP, ADAPTIVE_MAX_TOTAL_DRIFT,
ADAPTIVE_RIDGE_ALPHA,
VALIDATION_BAYES_SHRINK, VALIDATION_HITRATE_PRIOR_ALPHA / _BETA,
VALIDATION_IC_PRIOR_N.
```

### Tests
`tests/test_new_modules.py` adds nine tests covering: per-sector zero-mean
+ skipped-sector flag; turnover-monotone weights with survivor-IC only;
residual-IC rejects linear combos of survivors; adaptive dormant when
disabled, refuses per-symbol input, refuses look-ahead rows, and force-
dormants when total drift exceeds cap; Bayesian shrinkage moves small
samples toward prior; and the new fetchers are exposed.
