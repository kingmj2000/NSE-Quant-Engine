# Inspiration Map — Fincept Terminal & Vibe Trading

This project is a self-contained, offline NSE research engine. Several of its
professional-desk features were inspired by two open-source projects:

- **Fincept Terminal** — https://github.com/Fincept-Corporation/FinceptTerminal
- **Vibe Trading** — https://github.com/HKUDS/Vibe-Trading

None of their code is bundled. We reimplemented the *concepts* in pure
Python with our own signatures, guards and CSV/JSON artifacts so the whole
pipeline stays dependency-light, deterministic, and safe to fail.

## Concept → module → artifact → how to activate

| Source | Concept | Step | Module | Artifact in `output/` | How to activate / feed |
|---|---|---|---|---|---|
| Fincept | Macro regime dashboard | 4 | `core/regime.py`, `core/sentiment_overlay.py` | `macro_context.json` | Always on. Uses ^NSEI. |
| Fincept | Sector & peer research desk | 10 | `core/sector_context.py` | `top5_sector_context.csv` | Always on. `data/fundamentals_latest.csv` is now **auto-fetched via yfinance** at the start of every run (Step 0.5, `core/optional_data_fetchers.py`); drop your own CSV to override. |
| Fincept | Earnings / event calendar tab | 11 | `core/event_calendar.py` | `top5_events.csv` | Always on. `data/earnings_calendar.csv` (`Symbol,Event_Date`) is **auto-fetched via `yfinance.Ticker.calendar`** at the start of every run; drop your own CSV to override. |
| Fincept | Institutional flow panel (FII/DII + bulk deals) | 14 | `core/institutional_flow.py` | `top5_institutional_flow.csv`, merged into `macro_context.json` | Both `data/fii_dii_daily.csv` (Moneycontrol) and `data/bulk_deals.csv` (NSE JSON) are **auto-fetched at the start of every run**. Sources fail soft — if a public site is down the last-good cache is kept and `fii_regime` reads `Unknown`. |
| Vibe | Multi-alpha "zoo" with IC survivorship | 5 | `core/alpha_zoo.py`, `core/alpha_evaluator.py` | `alpha_zoo_ic_report.csv`, `alpha_zoo_survivors.json` | Always on when history is long enough. |
| Vibe | Risk-parity / vol-target sizing | 8 | `core/position_sizer.py` | `top5_position_sizing.csv` | Tune `PORTFOLIO_NAV_INR`, `PORTFOLIO_VOL_TARGET`, `MAX_WEIGHT`, `CASH_BUFFER` in `core/config.py`. |
| Vibe | Walk-forward "style" backtest | 9 | `core/backtest_engine.py` | `backtest_scorecard.csv`, `backtest_equity_curve.csv` | Auto-runs when price history ≥ ~260 sessions. |
| Vibe | Expected value / fractional-Kelly cross-check | 12 | `core/expected_value.py` | `top5_expected_value.csv` | Uses backtest hit-rate + horizon expected return. Report-only. |
| Vibe | Portfolio-level ship/hold gate | 13 | `core/portfolio_validation.py` | `portfolio_validation.json` | Thresholds live in `core/config.py` (`PV_*`). |
| Vibe | Regime-conditional alpha tilt | 15 | `core/regime_tilt.py` | `regime_tilt_report.json` | Report-only by default. Set `REGIME_TILT_APPLY=1` in config to make it change scoring weights. |
| Vibe | Turnover vs round-trip-cost gate | 16 | `core/rebalance_diff.py` | `rebalance_diff.json` | Compares to `output/prev_top5_snapshot.csv`. First run shows 100% turnover, then it stabilises. |
| Both | Portable evidence bundle for external LLM | 7 | `core/evidence_bundle.py` + `prompts/rationale_prompt.md` | `insight_bundle_<ts>.zip` (contains `README_for_AI.md`) | Always on. Upload the zip to Claude/any LLM; the baked prompt does the rest. |

## Why some steps look empty in a given run

If you see `fii_regime=Unknown`, `0 in-window`, or `0 scored`, the step ran
correctly but had no input data to work with. Add the optional CSVs from the
table above to `data/` and rerun — the pipeline is designed so missing inputs
never break the build; they just make that overlay quiet.

## Deliberate non-goals

We did **not** copy live-terminal features from Fincept (real-time
websockets, broker adapters, chat UI) or live-trading loops from Vibe
Trading (execution, order routing, RL training). This project stays a
decision-support research engine that hands a full evidence pack to a human
+ external LLM.
