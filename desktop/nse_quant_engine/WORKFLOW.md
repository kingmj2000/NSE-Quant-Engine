# NSE Insight Engine — Workflow (Steps 1–16)

End-to-end pipeline for identifying top short-hold investment opportunities from
the NSE Stock & ETF universe, with a portable evidence pack for an external
AI analyst (Claude / any LLM) — no runtime AI, no cloud, no API keys.

## Run

- **Windows:** `run_app.bat` (interactive menu) or `run_full_workflow.bat`
- **Mac/Linux:** `./run_app.command`
- **Manual:** `python run_app.py`

Everything below runs in one pass. Kill-switch: `INSIGHT_SAFE_MODE=1` disables
Steps 3–16 in one shot; Steps 1–2 always run.

## Pipeline

| # | Step | Module | Output |
|---|---|---|---|
| 1 | Universe & prices | `universe_builder.py`, `core/price_cache.py` | `config.csv`, `data/raw_prices_latest.csv` |
| 2 | Scoring + correlation-aware top-5 | `nse_quant_engine.py`, `core/portfolio_selection.py` | `latest_scores.csv`, `top5_corr_matrix.csv`, `top5_benchmark_stats.csv` |
| 3 | Hold-horizon optimizer | `core/horizon_optimizer.py` | `top5_horizon.csv` |
| 4 | Sentiment + macro regime | `core/sentiment_overlay.py`, `core/regime.py` | `top5_sentiment.csv`, `macro_context.json` |
| 5 | Alpha zoo IC evaluation | `core/alpha_evaluator.py` | `alpha_zoo_ic_report.csv`, `alpha_zoo_survivors.json` |
| 6 | Fundamentals & quality overlay | `core/fundamentals_overlay.py` | `top5_fundamentals.csv` |
| 7 | Evidence bundle (AI handoff) | `core/evidence_bundle.py` | `insight_bundle_<ts>.zip` |
| 8 | Risk-parity position sizing | `core/position_sizer.py` | `top5_position_sizing.csv` |
| 9 | Walk-forward backtest | `core/backtest_engine.py` | `backtest_scorecard.csv`, `backtest_equity_curve.csv` |
| 10 | Sector & peer context | `core/sector_context.py` | `top5_sector_context.csv` |
| 11 | Event & catalyst calendar | `core/event_calendar.py` | `top5_events.csv` |
| 12 | Expected value / Kelly cross-check | `core/expected_value.py` | `top5_expected_value.csv` |
| 13 | Portfolio-level validation gate | `core/portfolio_validation.py` | `portfolio_validation.json` |
| 14 | Institutional flow overlay | `core/institutional_flow.py` | `top5_institutional_flow.csv` |
| 15 | Regime-conditional alpha tilt | `core/regime_tilt.py` | `regime_tilt_report.json` |
| 16 | Rebalance / turnover diff | `core/rebalance_diff.py` | `rebalance_diff.json` |

All outputs land in `output/`. Steps 7–16 are automatically packaged into the
zip bundle for the LLM handoff.

## AI handoff (offline)

The script never calls an LLM. Instead, at the end of every run it produces:

```
output/insight_bundle_<YYYYMMDD_HHMM>.zip
  ├── top5.csv, top5_*.csv           (all per-pick evidence)
  ├── evidence.json                  (aggregated, LLM-friendly)
  ├── portfolio_validation.json
  ├── regime_tilt_report.json
  ├── rebalance_diff.json
  ├── run_manifest.json              (timestamp, config, engine version)
  └── README_for_AI.md               (system prompt — see prompts/rationale_prompt.md)
```

Upload the zip to Claude (or any LLM) and use `README_for_AI.md` as the system
prompt. The model returns strict JSON with per-pick thesis, risks, event risk,
institutional-flow confirmation, EV sanity check, batch verdict, and a
rotate-vs-hold recommendation. No dependency on any hosted AI at runtime.

## Optional local data files

Drop these into `data/` to activate Step 14 (institutional flow):
- `fii_dii_daily.csv` — columns: `Date, FII_Net_INR_Cr, DII_Net_INR_Cr`
- `bulk_deals.csv`   — columns: `Date, Symbol, Client, Buy_Sell, Qty, Price`

Both are optional; missing files → `Institutional_Confirmation=Unknown`, pipeline
unchanged.

## Kill switches (env vars)

| Env var | Default | Effect |
|---|---|---|
| `INSIGHT_SAFE_MODE` | 0 | 1 disables Steps 3–16 |
| `QUALITY_WEIGHT` | 0.0 | Fundamentals report-only unless raised |
| `REGIME_TILT_APPLY` | 0 | 1 applies regime tilt to scoring (default report-only) |
| `KELLY_OVERRIDE` | 0 | 1 uses Kelly cap over risk-parity weights |
| `BACKTEST_STALE_DAYS` | 7 | Skips backtest if last one is younger |

See `core/config.py` for the full list.
