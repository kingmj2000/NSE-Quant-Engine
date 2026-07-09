
# Plan — Signal upgrades + gated adaptive weighting (tightened)

All changes are deterministic Python. No LLM calls, no new network beyond
existing fetchers (NSE public endpoints + yfinance). Every new artifact
lands in `output/` and is picked up by `core/evidence_bundle.py` into the
zip your external AI reads. Part B ships **dormant**
(`ADAPTIVE_ENABLED=False`) and shadow-only until you manually enable.

---

## PART A — Signal upgrades

### A1. Confirm alpha survivor pipeline is live (pre-check)

Before adding candidates, verify at build time:
- `core/alpha_zoo.py` emits all candidates each run.
- `core/alpha_evaluator.py` writes `output/alpha_zoo_ic_report.csv` and
  `output/alpha_zoo_survivors.json` with **walk-forward** Spearman IC +
  t-stat per alpha (matured windows only — no in-sample IC anywhere).
- `core/scoring.py` reads `alpha_zoo_survivors.json` and only blends
  survivors into the live score.

Fix wiring before adding anything new. New alphas must flow through this
gate; nothing hardwired.

### A2. Sector-neutral scoring

Location: new `core/sector_neutralize.py`, called from
`compute_opportunity_scores` in `core/scoring.py`.

- Sector map from `data/fundamentals_latest.csv` / `core/sector_context.py`;
  fallback bucket "Unknown".
- For each raw factor in the live blend: within each sector, z-score
  members; **skip sectors with < 5 members** — those symbols stay
  universe-standardized only. Log skipped sectors (name + count) to
  `output/scoring_sector_neutralization.csv`.
- After per-sector z-score, re-standardize universe-wide so scales match.
- Config flag `SECTOR_NEUTRAL=True` (default on); reversible.
- Artifact: `scoring_sector_neutralization.csv` (per-symbol raw score,
  sector, sector-size, neutralized score, skipped-flag).

### A3. Turnover-aware alpha weights

Location: new `core/alpha_weighting.py`, called from `core/scoring.py`
after survivor gating.

- Baseline weights from `config.ALPHA_WEIGHTS`.
- **IC input is walk-forward survivor IC from `alpha_evaluator`
  (matured windows only)** — never current-period / in-sample IC. If
  survivor IC unavailable for an alpha, that alpha gets baseline weight
  only (no turnover adjustment).
- Turnover per alpha estimated from last N rebalance snapshots
  (`output/rebalance_diff.json` history + `prev_top5_snapshot.csv`); if
  no history yet, turnover = 0.
- `w_i ∝ IC_i / (1 + λ · turnover_i)`, `λ = TURNOVER_LAMBDA` (default
  small). Normalize to sum to 1.
- Artifact: `output/alpha_weights_current.json` (baseline, survivor IC,
  turnover, final weight per alpha).

### A4. Delivery % candidate alpha

Data: NSE `sec_bhavdata_full` daily CSV (has `%DlyQtToTradedQty`). Reuse
`_nse_warmup` from `optional_data_fetchers.py`.

- New `fetch_delivery_pct` in `optional_data_fetchers.py`:
  - Warm-up + GET bhavcopy for latest N trading days.
  - **Append** to `data/delivery_pct_daily.csv` (dedupe on
    `Date, Symbol`); never overwrite existing rows.
  - Backoff on 5xx (2 retries, 2s→4s). **Fail-soft**: exception logged,
    cached CSV untouched, pipeline continues.
- New candidate alpha `delivery_momentum` in `core/alpha_zoo.py`:
  5d vs 20d rolling delivery-% change.
- Runs through `alpha_evaluator` like any candidate.

### A5. IV rank candidate alpha

Data: NSE `/api/option-chain-equities` for F&O underlyings; ATM IV
percentile vs trailing 252 sessions of ATM IV.

- New `fetch_iv_rank` in `optional_data_fetchers.py`:
  - Warm-up + per-symbol option-chain fetch (F&O universe only;
    non-F&O rows stay NaN).
  - **Append** to `data/iv_rank_daily.csv` (dedupe on `Date, Symbol`);
    same backoff + fail-soft rules as A4. A failed run never blocks the
    pipeline or wipes cached data.
- New candidate alpha `iv_rank` in `alpha_zoo.py` (sign learned by
  evaluator).

### A6. Incremental-IC gate (residual-based)

Location: `core/alpha_evaluator.py`, promotion step.

- For each candidate alpha, regress its per-date z-scores on the current
  survivor set's z-scores (walk-forward, matured windows only) and take
  the residual.
- Compute Spearman IC of the **residual** vs forward returns.
- Candidate promotes only if:
  - standalone IC ≥ `ALPHA_IC_MIN` AND t-stat ≥ `ALPHA_TSTAT_MIN`
    (existing gates), AND
  - **residual IC ≥ `ALPHA_INCREMENTAL_IC_MIN`** (new config, default
    e.g. 0.015).
- Rejects candidates that are merely correlated with existing survivors.
- Log both standalone IC and residual IC to `alpha_promotion_log.json`.

### A7. Evidence bundle logging

Extend `core/evidence_bundle.py` to include:
- `alpha_zoo_ic_report.csv` (verify present).
- New `alpha_promotion_log.json`: per candidate `{standalone_ic,
  standalone_tstat, residual_ic, decision, reason}`.
- `alpha_weights_current.json` (from A3).
- `scoring_sector_neutralization.csv` (from A2).

Excluded per your spec: PCR, max-pain, dashboard panels, alerts.json.

---

## PART B — Adaptive alpha weighting (dormant, guardrailed)

Location: new `core/adaptive_weights.py`, plus shadow hook in
`nse_quant_engine_v4_shadow.py`.

Guardrails (all mandatory):

1. **Walk-forward only.** Fit uses `output/forward_return_history.csv`
   rows with matured forward window strictly before target date T.
   Assert this on every fit; refuse otherwise.
2. **Gated on validation.** Read `output/validation_status.json`.
   Dormant unless verdict = "Validation Positive" AND
   **effective (overlap-adjusted) validation dates ≥
   `ADAPTIVE_MIN_DATES` (default 60)** — raw row count is not enough.
   When dormant, log `"adaptive: dormant, insufficient effective
   history (N_eff=<x>, need <y>)"`.
3. **Shadow-first.** Fitted weights written to
   `output/adaptive_weights_shadow.json`, consumed only by
   `nse_quant_engine_v4_shadow.py`. Primary uses fixed baseline.
   `shadow_vs_official_report.py` tracks shadow-vs-primary net-of-cost
   over runs. Promotion is manual.
4. **Heavily regularized.**
   - `final = (1-α)·baseline + α·fitted`, `α = ADAPTIVE_SHRINKAGE_ALPHA`
     (default 0.20).
   - Per-weight step cap `ADAPTIVE_MAX_STEP` (default 0.05 abs / run).
   - **Total-drift cap `ADAPTIVE_MAX_TOTAL_DRIFT` (default 0.30):** if
     `Σ|fitted_i − baseline_i| > cap`, force dormant this run and warn
     in the log.
5. **No per-symbol learning.** Universe-level factor weights only.
   Assert inputs are keyed by alpha, not by symbol; refuse otherwise.
6. **Fully logged & reversible.** Master flag `ADAPTIVE_ENABLED=False`
   (config default). Each run writes `output/adaptive_weights_log.json`:
   `{date, baseline, fitted_raw, shrunk_final, per_weight_delta,
   total_drift, validation_gate_status, N_eff, shadow_or_primary,
   dormant_reason?}`. Included in evidence bundle.

Fit method: ridge regression of per-date forward returns on per-alpha
z-scores (matured only), ridge alpha in config. No neural nets, no
per-symbol features.

---

## Bayesian shrinkage on validation stats (protects ship/hold gate)

Location: `core/validation_status.py` (or `cross_sectional_validation.py`,
wherever hit-rate / IC point estimates land).

- Confirm — and add if missing — Bayesian shrinkage toward prior on
  hit-rate and IC before the ship/hold decision:
  - Hit-rate: Beta prior (e.g. Beta(α₀=10, β₀=10) ≈ 50%), posterior
    mean used in gate.
  - IC: shrink toward 0 with weight ~ prior_n / (prior_n + observed_n),
    `prior_n = VALIDATION_IC_PRIOR_N` (default 20).
- This is **separate from adaptive-weight shrinkage** and always on. It
  protects the ship/hold verdict from small-sample noise regardless of
  Part B state.
- Log both raw and shrunk values to `validation_status.json` for
  transparency.

---

## Config additions (`core/config.py`)

```python
SECTOR_NEUTRAL = True
SECTOR_NEUTRAL_MIN_MEMBERS = 5
TURNOVER_LAMBDA = 0.25
ALPHA_INCREMENTAL_IC_MIN = 0.015

ADAPTIVE_ENABLED = False
ADAPTIVE_MIN_DATES = 60          # effective (overlap-adjusted) dates
ADAPTIVE_SHRINKAGE_ALPHA = 0.20
ADAPTIVE_MAX_STEP = 0.05
ADAPTIVE_MAX_TOTAL_DRIFT = 0.30
ADAPTIVE_RIDGE_ALPHA = 1.0

VALIDATION_BAYES_SHRINK = True
VALIDATION_HITRATE_PRIOR_ALPHA = 10
VALIDATION_HITRATE_PRIOR_BETA  = 10
VALIDATION_IC_PRIOR_N = 20
```

## Files touched

New: `core/sector_neutralize.py`, `core/alpha_weighting.py`,
`core/adaptive_weights.py`.
Edited: `core/config.py`, `core/scoring.py`, `core/alpha_zoo.py`,
`core/alpha_evaluator.py` (residual-IC gate + walk-forward IC exposure),
`core/optional_data_fetchers.py` (delivery %, IV rank; append + backoff +
fail-soft), `core/validation_status.py` (Bayesian shrinkage),
`core/evidence_bundle.py`, `nse_quant_engine_v4_shadow.py`,
`shadow_vs_official_report.py`, `orchestrator.py`, `WORKFLOW.md`,
`INSPIRATION_MAP.md`, `CHANGES_v4_3.md`.
Data (fetched, cached, append-only): `data/delivery_pct_daily.csv`,
`data/iv_rank_daily.csv`.
Output (per run): `alpha_promotion_log.json`,
`alpha_weights_current.json`, `scoring_sector_neutralization.csv`,
`adaptive_weights_shadow.json`, `adaptive_weights_log.json`, updated
`validation_status.json` (raw + shrunk stats).

## Validation

- `tests/test_new_modules.py`:
  - sector-neutralization: zero-mean per sector for sectors ≥ 5, skipped
    sectors flagged;
  - turnover-aware weights monotone in turnover, use survivor IC only;
  - residual-IC gate rejects a candidate that is a linear combo of
    survivors;
  - adaptive fit refuses look-ahead rows; enforces per-step cap AND
    total-drift cap; `ADAPTIVE_ENABLED=False` = zero effect;
  - fetchers append-and-dedupe, and a simulated 5xx never wipes cache;
  - Bayesian shrinkage moves small-sample hit-rate toward prior.
- One end-to-end run: alpha_promotion_log lists delivery% and IV rank
  with standalone + residual IC and decision; adaptive log says
  "dormant, insufficient effective history"; evidence zip contains all
  new artifacts.

## Scope

All of the above fits one build because Part B ships dormant. Only
plausible reason for a follow-up run: if the NSE option-chain endpoint
is heavily rate-limited in your environment, A5 may need per-symbol
throttling tuning in a second pass. Everything else lands in this build.
