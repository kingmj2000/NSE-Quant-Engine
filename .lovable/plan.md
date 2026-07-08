# Steps 6–9 — Close the gap to a PM-grade NSE opportunity engine (local-only, AI handoff via zip)

Steps 1–5 delivered alpha-zoo, correlation-aware top-5, benchmark/IR, hold-horizon optimizer, sentiment/macro overlay, and walk-forward alpha IC evaluation.

Steps 6–9 finish the borrow-list from **Fincept Terminal** (breadth: fundamentals, sector rotation, macro tiles, backtesting) and **Vibe-Trading** (discipline: factor evaluation, horizon-aware ranking, analyst-style rationale) — but **the local script never calls any AI**. Instead we produce a **portable evidence bundle** (`output/insight_bundle_<timestamp>.zip`) plus a **prompt spec Markdown** for Claude / any LLM. The user drops the zip into their chat with the model manually — that's the only AI step, and it's off-machine.

All work stays inside `desktop/nse_quant_engine/`, pure-pandas + stdlib, no new pip deps, every module feature-flagged and wrapped in `try/except` in `trade_plan_builder.py` so the base pipeline can never regress. `INSIGHT_SAFE_MODE=1` disables Steps 6–9 in one shot.

---

## Step 6 — Fundamentals & Quality Overlay (Fincept)

**Why.** Today we rank on price/vol/technicals. A PM never surfaces a top-5 without a fundamentals/valuation sanity check.

**New:** `core/fundamentals_overlay.py`
- Reuses cached fundamentals already fetched by `core/fundamental_factor.py` (no extra network fetch) — for the shortlisted universe only.
- Per symbol: `ROE_TTM`, `DebtToEquity`, `EPS_Growth_YoY`, `PE_TTM`, `PEG`, `EarningsSurprise_Last4Q`, `PromoterPledgePct` (if present, else NaN — never fabricated).
- `quality_score()` → z-score blend in `[-3, +3]`.
- `valuation_flag()` → `Cheap / Fair / Expensive` vs 3Y self-median PE **and** sector median.
- ETFs bypass and route to `etf_microstructure.py` (TER, tracking-error, AUM, liquidity).

**Wiring:** `trade_plan_builder.py` merges into candidate frame. Blend into `Final_Score` gated by `QUALITY_WEIGHT` (default **0.0** — report-only until IC on `Quality_Score` is reviewed; user flips to 0.10 after eyeballing). Emits `output/top5_fundamentals.csv`. Dashboard: quality/valuation chip + a one-line "why this passes fundamentals".

**Config:** `FUNDAMENTALS_OVERLAY_ON=True`, `QUALITY_WEIGHT=0.0`, `VALUATION_LOOKBACK_YEARS=3`.

---

## Step 7 — Evidence Bundle + AI Prompt Spec (Vibe-Trading, no in-script AI)

**Why.** Vibe-Trading's core value is analyst-style rationale. We keep that value but **move the AI call off-machine**: the script assembles a self-contained zip the user hands to Claude / any LLM.

**New:** `core/evidence_bundle.py` (pure stdlib `zipfile`, `json`, `csv`)
- Collects into `output/insight_bundle_<YYYYMMDD_HHMM>.zip`:
  - `top5.csv` — final ranked picks with all scores/flags
  - `top5_fundamentals.csv`, `top5_horizon.csv`, `top5_sentiment.csv`, `top5_benchmark_stats.csv`, `top5_corr_matrix.csv`, `top5_position_sizing.csv`, `alpha_zoo_survivors.json`, `alpha_zoo_ic_report.csv`, `macro_context.json` (all produced by Steps 2–8 — included only if the file exists)
  - `evidence.json` — one compact record per top-5 name aggregating every signal so the LLM never has to cross-join CSVs
  - `README_for_AI.md` — see below
  - `run_manifest.json` — timestamp, engine version, config snapshot, universe size, data-quality summary, missing-data disclosures
- Zip stays small (target < 2 MB): no raw OHLC price history, no full universe — only the shortlisted names' evidence.

**New:** `prompts/rationale_prompt.md` (committed template, copied into every zip as `README_for_AI.md`)
- Role: "senior PM writing a client-facing pick note".
- Rules: no fabrication, cite only the CSV/JSON columns provided, mark unknowns as unknown, one thesis + key risks + invalidation trigger per pick, sanity-check that the recommended horizon aligns with the risk cap, flag any pick where signals contradict.
- Output contract: strict JSON schema (`{ symbol, thesis[], risks[], invalidation, confidence: low|med|high, contradictions[] }`) so the user can paste the response back and future Step 7.5 can render it into the dashboard.

**Wiring:** `trade_plan_builder.py` calls `build_bundle()` at the very end of the run. Prints the zip path + a one-line instruction: *"Upload this zip to Claude with the included README_for_AI.md as the system prompt."*

**Config:** `EVIDENCE_BUNDLE_ON=True`, `BUNDLE_MAX_MB=5`, `BUNDLE_KEEP_LAST_N=10` (auto-prune older zips).

**No pip deps, no API keys, no network.** The engine remains 100% local.

---

## Step 8 — Risk-Parity / Vol-Targeted Position Sizing (both repos)

**Why.** We emit target/stop prices but no position sizes. A PM allocates by risk contribution, not equal weight.

**New:** `core/position_sizer.py`
- Inputs: top-5 list, 63D realised vol per name, pairwise correlation matrix from Step 2, `PORTFOLIO_VOL_TARGET=0.12`, `MAX_WEIGHT=0.30`, `CASH_BUFFER=0.10`.
- Modes:
  1. `inverse_vol` — weights ∝ 1/σ, capped, re-normalised.
  2. `risk_parity_lite` — 50-iteration Newton solve for equal risk contribution using the existing corr matrix (numpy only; no `cvxpy`).
- Output per name: `Weight_%`, `Capital_INR` (given `PORTFOLIO_NAV_INR`), `Risk_Contribution_%`, `Stop_Loss_INR`, `Max_Loss_INR` = weight × stop distance.

**Wiring:** `output/top5_position_sizing.csv`. Dashboard: allocation donut (inline SVG) + per-card weight / risk-contribution chip + "Max loss if stop hits" line.

**Config:** `POSITION_SIZER_ON=True`, `SIZING_MODE=risk_parity_lite`, `PORTFOLIO_VOL_TARGET=0.12`, `PORTFOLIO_NAV_INR=1000000`, `MAX_WEIGHT=0.30`, `CASH_BUFFER=0.10`.

---

## Step 9 — Walk-Forward Backtest & Engine Scorecard (Fincept)

**Why.** We evaluate individual alphas (Step 5) but never the *final top-5 selection*. Without a rolling scorecard the user can't judge whether the engine's picks would have worked.

**New:** `core/backtest_engine.py`
- Replays the last 250 sessions. For each historical `t`, reconstruct the engine's top-5 using only data ≤ t (strict no-lookahead — reuses the guards from `alpha_evaluator.py`), hold for the recommended horizon, mark-to-market.
- Metrics: `Hit_Rate`, `AvgWin_%`, `AvgLoss_%`, `Payoff_Ratio`, `Sharpe`, `Sortino`, `MaxDD_%`, `AvgHoldDays`, `TurnoverPerMonth`, `Excess_vs_Nifty_%`.
- Emits "top-1 only" and "top-5 equal-weight" variants for comparison.
- Cached: skips rebuild if the last artifact is < `BACKTEST_STALE_DAYS=7` old.

**Wiring:** `output/backtest_scorecard.csv` + `output/backtest_equity_curve.csv`. Dashboard: "Engine Track Record" section with inline SVG equity curve, hit rate, drawdown. Included in the Step 7 zip so the AI can cite it.

**Config:** `BACKTEST_ON=True`, `BACKTEST_LOOKBACK_DAYS=250`, `BACKTEST_STALE_DAYS=7`.

---

## Files touched

```text
core/fundamentals_overlay.py    (NEW)
core/evidence_bundle.py         (NEW)
core/position_sizer.py          (NEW)
core/backtest_engine.py         (NEW)
prompts/rationale_prompt.md     (NEW — packaged into every zip)
core/config.py                  (+ knobs for all four steps + INSIGHT_SAFE_MODE extension)
trade_plan_builder.py           (call new modules, all wrapped in try/except; final build_bundle call)
dashboard_html_builder.py       (quality/valuation chip, allocation donut, backtest scorecard tile, "bundle ready" banner with zip path)
tests/test_new_modules.py       (+ ~10 tests: quality z-score sanity, risk_parity_lite identity-corr, backtest no-lookahead assertion, bundle zip contents + schema, prompt file present)
```

## Guardrails (unchanged contract)

- Every new stage wrapped in `try/except` in `trade_plan_builder.py` / `dashboard_html_builder.py`; failure hides the tile, pipeline continues.
- All feature flags default **on** (except `QUALITY_WEIGHT=0.0` — report-only). `INSIGHT_SAFE_MODE=1` disables Steps 6–9 in one shot.
- **No new pip deps. No network calls beyond what Steps 1–5 already do. No AI API calls anywhere in the script.**
- No changes to `orchestrator.py` step order, `run_app.py`, or existing CSV schemas — only additive outputs.

## Rollout order within this batch
6 → 8 → 9 → 7  (fundamentals first so sizing & backtest can consume `Quality_Score`; evidence bundle last so it packages everything produced above).

## Validation before hand-off
- `python tests/test_new_modules.py` — full suite green.
- `py_compile` on every modified file.
- End-to-end dry run: `python run_app.py` completes with **all `ok`**, `output/insight_bundle_*.zip` exists and contains `README_for_AI.md` + `evidence.json` + all expected CSVs, zip size < 5 MB.
- Manual smoke: unzip the bundle, upload to Claude with the README as system prompt, confirm it returns valid JSON matching the schema.

## Out of scope (flagged for a later ask, not built now)
- Options overlay / hedging (needs NSE options feed we don't wire today).
- Multi-agent LLM debate loop (Vibe-Trading pattern; would require in-script AI — user explicitly excluded).
- Live paper-trading loop (broker-API territory, product decision).

Reply **"go"** to build Steps 6 → 8 → 9 → 7 in that order, or name specific steps to keep/skip.
