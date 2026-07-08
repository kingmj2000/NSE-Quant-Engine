
# Steps 3–5 — Bring the Insight Engine closer to a "top-5, low-risk, short-hold" advisor

All three steps stay pure-Python, feature-flagged, and crash-isolated. Nothing changes in `orchestrator.py` step order or `run_app.py`. Alpha Zoo (Step 1) stays gated until Step 5 validates it.

Inspiration mapping:
- **Fincept Terminal** → breadth of market context: macro/news/sentiment tiles, benchmark overlays, sector rotation views. We borrow the *layout idea* and *free-data ingestion pattern*, not the code.
- **Vibe-Trading** → factor-zoo evaluation, walk-forward IC/IR scoring of signals, and horizon-aware ranking. We borrow the *evaluation discipline* (IC per factor per horizon), not the LLM agent stack.

---

## Step 3 — Hold-Horizon Optimizer (per candidate)

**Why.** Today every top-5 pick is scored as a generic "buy now" with a fixed target/stop. Goal is *shortest hold* — so each candidate needs its own recommended horizon and an expected risk/return at that horizon.

**New module:** `core/horizon_optimizer.py`
- `horizon_grid = [3, 5, 10, 21, 42, 63]` sessions
- `expected_return_curve(prices, horizons)` — historical median forward return + IQR by horizon (from past N=250 sessions, computed only from **realized** history — no lookahead).
- `risk_curve(prices, horizons)` — realized vol × √h and max historical drawdown per horizon.
- `optimal_horizon(candidate) -> {h, exp_ret, risk, sharpe_like}` — pick horizon that maximises `median_ret / (downside_vol + eps)` subject to `risk ≤ RISK_CAP_PER_HORIZON`.

**Wiring:** `trade_plan_builder.py` calls `optimal_horizon` per top-5 name, writes `output/top5_horizon.csv` with columns `[Symbol, Rec_Horizon_Days, Exp_Ret_%, Downside_Vol_%, Sharpe_like, Target_Price, Stop_Price]`. Existing target/stop logic stays as fallback; new values override only when confidence flag set.

**Dashboard tile (`dashboard_html_builder.py`):**
- New "Recommended hold" chip on each top-5 card: `≈8 sessions · +3.2% exp · −1.4% vol · S≈1.9`.
- Sparkline (pure inline SVG) of the expected-return curve across horizons so the user can *see* why 8 was chosen.

**Config (`core/config.py`):**
```
HORIZON_OPTIMIZER_ON = True
HORIZON_GRID         = [3,5,10,21,42,63]
HORIZON_HIST_DAYS    = 250
RISK_CAP_PCT         = 6.0
```

**Tests (`tests/test_new_modules.py`):**
- Synthetic upward-drift series → optimal horizon > 1, exp_ret > 0.
- Flat noise series → sharpe_like ≈ 0, function returns safely.
- No-lookahead source check (grep for `.shift(-`).

---

## Step 4 — Sentiment & Macro Context Overlay (Fincept-inspired)

**Why.** Right now scoring is purely quantitative on price/volume/fundamentals. A short-hold recommendation is much safer if we also know: is there *recent adverse news*, is the *sector regime supportive*, and is the *macro tape risk-on*.

**No new pip deps.** VADER is skipped for now — we reuse the existing `news_market_builder.py` output and add a lightweight keyword-polarity scorer in pure Python (curated positive/negative lexicon of ~120 finance terms shipped as `core/data/lexicon_finance.csv`).

**New module:** `core/sentiment_overlay.py`
- `score_headlines(df_news) -> pd.DataFrame[Symbol, Headlines_7D, PosPct, NegPct, Net_Sent]`
- `macro_tape_score()` reads existing benchmark + India VIX (already in cache when available) → returns `{regime: 'risk-on'|'neutral'|'risk-off', vix_level, nifty_50d_trend}`.
- `sector_rotation_table(sector_returns_21d)` → ranks sectors by 21D relative strength vs Nifty.

**Wiring:**
- `trade_plan_builder.py` — join `Net_Sent` per top-5 symbol; **veto flag** (not score change) if `NegPct ≥ 0.6` AND `Headlines_7D ≥ 3` → candidate demoted out of top-5 and replaced from pool. Feature-flag `SENTIMENT_VETO_ON=True`.
- Emit `output/top5_sentiment.csv` and `output/macro_context.json`.

**Dashboard:**
- New top-of-page **"Market Context" strip** (three tiles): Macro tape (VIX + trend), Sector rotation top-3/bottom-3, Breadth (adv/dec if available else N/A).
- Per-card **News chip**: `📰 5 headlines · 🟢 60% / 🔴 20%` with hover-title listing latest 3 headlines.

**Config:**
```
SENTIMENT_OVERLAY_ON = True
SENTIMENT_VETO_ON    = True
SENT_LOOKBACK_DAYS   = 7
SENT_NEG_VETO_PCT    = 0.60
SENT_MIN_HEADLINES   = 3
```

**Tests:** lexicon parses; polarity function returns [-1,1]; veto triggers on synthetic negative-heavy news frame.

---

## Step 5 — Alpha-Zoo Validation & Gated Wiring (Vibe-Trading-inspired)

**Why.** Step 1 delivered 15 alphas but they are not in scoring. Wiring them blind would degrade quality. This step evaluates each alpha's **Information Coefficient (IC)** per horizon on the last 250 sessions and only promotes the survivors.

**New module:** `core/alpha_evaluator.py`
- `evaluate_alphas(prices_long, alpha_df, horizons=[5,10,21]) -> pd.DataFrame`
  - Per (alpha, horizon): Spearman IC, IC t-stat, hit rate, decay curve, turnover proxy.
  - Walk-forward: rolling 60/20 train/test folds, aggregate mean IC.
- `promote_alphas(eval_df, min_ic=0.03, min_t=2.0) -> list[str]` — survivor list.
- Writes `output/alpha_zoo_ic_report.csv` and `output/alpha_zoo_survivors.json`.

**Wiring into scoring (`core/scoring.py`):**
- New composite `AlphaZoo_Score` = equal-weight z-score of survivors only.
- Blended into `Final_Score` with weight `ALPHA_ZOO_WEIGHT` (default **0.10**, i.e. 10% of composite). Existing weights renormalise so behaviour is a controlled tilt, not a rewrite.
- Feature-flag `ALPHA_ZOO_ON=True` **but** if `len(survivors) < 3` → auto-disable and log; pipeline unaffected.

**Dashboard:**
- New "Alpha Zoo — surviving signals" tile listing survivors with their IC and horizon, plus a mini bar chart. Non-technical caption: *"These 6 signals independently predicted 5–21 day moves over the last 12 months."*

**Config:**
```
ALPHA_ZOO_ON       = True
ALPHA_ZOO_WEIGHT   = 0.10
ALPHA_IC_MIN       = 0.03
ALPHA_TSTAT_MIN    = 2.0
ALPHA_EVAL_DAYS    = 250
ALPHA_EVAL_FOLDS   = 4
```

**Tests:**
- Synthetic price series with a hand-crafted predictive factor → IC > 0.1, promoted.
- Pure-noise factor → not promoted.
- Scoring integration: with survivors=[] → Final_Score identical to pre-wire baseline.

---

## Files touched (all steps)

```text
core/horizon_optimizer.py       (NEW)
core/sentiment_overlay.py       (NEW)
core/alpha_evaluator.py         (NEW)
core/data/lexicon_finance.csv   (NEW, ~120 terms)
core/config.py                  (+ knobs above)
core/scoring.py                 (Alpha Zoo blend, gated)
trade_plan_builder.py           (call optimizer, sentiment veto, emit CSVs)
dashboard_html_builder.py       (horizon chip+sparkline, market-context strip,
                                  news chip, alpha-zoo tile)
tests/test_new_modules.py       (+ ~8 tests across the three modules)
```

## Guardrails (unchanged contract)

- Every new stage wrapped in `try/except` in `trade_plan_builder.py` and `dashboard_html_builder.py`; on failure the tile is hidden, pipeline continues.
- All feature flags default **on** but a single env override `INSIGHT_SAFE_MODE=1` disables Steps 3–5 in one shot.
- No new pip deps. No changes to `orchestrator.py` order, `run_app.py`, or existing CSV schemas — only additive outputs.

## Rollout order within this batch

1. Step 3 (horizon) — smallest surface, highest user-visible value.
2. Step 4 (sentiment/macro) — depends only on existing `news_market_builder.py` output.
3. Step 5 (alpha validation + wire) — last, because it touches `scoring.py`.

## Validation before hand-off

- `python tests/test_new_modules.py` — full suite green (existing + ~8 new).
- `py_compile` on every modified file.
- End-to-end dry run: `python run_app.py` completes with **all `ok`** in the summary; new files present in `output/`; dashboard HTML contains new tile ids (`#tile-horizon`, `#tile-market-context`, `#tile-alpha-zoo`).
- Manual eyeball of `output/alpha_zoo_ic_report.csv` — sanity-check IC magnitudes.

## Confirm to proceed

Green-light Steps 3–5 as scoped, or adjust (e.g. skip sentiment veto and keep it display-only, or defer Alpha-Zoo wiring until IC report is reviewed manually first)?
