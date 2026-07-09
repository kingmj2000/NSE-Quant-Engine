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
