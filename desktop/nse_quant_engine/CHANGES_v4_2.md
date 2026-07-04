# NSE Quant Engine — v4.2 Patch Notes

## One-button local runner
- **`run_app.py`** (PySide6) — single window, one Run button, live log, tabs for Scores / Shadow / Compare / DQ Report / Validation / Trade Plan.
- **`orchestrator.py`** — headless pipeline runner. Used by the GUI and by `run_app.bat` / `run_app.command`. CLI:
  ```
  python orchestrator.py --all
  python orchestrator.py --all --skip-fetch
  python orchestrator.py --steps nse_quant_engine,nse_quant_engine_v4_shadow,shadow_vs_official_report
  ```
- Always runs the official engine **and** the v4.1 shadow side-by-side; writes a champion **recommendation** but never auto-promotes.

## Data-quality cleanup
- **`core/data_quality.py`** — fixed flag enum: `OK / MISSING_TER / MISSING_TRACKING / STALE_NAV / LOW_AUM / UNRESOLVED_MAPPING / PRICE_GAP`. One pipe-joined `Quality_Flags` column plus per-flag booleans for filtering.
- **`dq_report_builder.py`** — writes `output/dq_report.md` + `data/dq_metrics.csv` + `data/dq_summary.json`. Fill-rate per field, flag distribution, source mix, top unresolved mappings, single Data Health Score (0–100).
- Smoke run on the bundled cache: TER 100% / AUM 100% / Mapping resolved on 327/328 — so the noise was a taxonomy/precedence issue, not a fetch failure.

## New factor families (all four picked in plan)
- **`core/regime.py`** — India VIX 252-day percentile → `LOW / MID / HIGH`. Scales `VOL_PENALTY_MAX`, `MIN_ABS_RETURN_21D`, `RS_FAIL_MULT`. Plus market breadth (`% above 50D MA`).
- **`core/sector_context.py`** — yfinance sector → NSE sector index (`^CNXIT`, `^CNXBANK`, …). `sector_rs_multiplier()` + geometric combine with market RS.
- **`core/etf_microstructure.py`** — 20D median traded value, intraday H-L spread % (bid-ask proxy), iNAV premium z-score, configurable liquidity floor (default ₹2 cr/day) and ±2.5σ premium cap.
- Fundamentals expansion left as an additive hook on the existing `core/fundamental_factor.py` (FCF yield / earnings revisions / Piotroski-lite slots are in the config; coverage guard already enforced).

## Analytical fixes (root-cause)
- `cross_sectional_validation.py` — now writes the canonical `output/validation_status.json` after every run (single source of truth).
- `trade_plan_builder.py` — reads `validation_status.json` first; markdown-scrape is now a fallback. Kills the false "Validation Positive" bug.
- Existing v4 fixes (no momentum triple-counting, neutral tracking-error, falling-knife cap, fundamentals coverage guard, EV gated on validation) remain wired through `core/`.

## Champion / shadow comparison
- **`shadow_vs_official_report.py`** — writes `output/shadow_vs_official.md` + `.csv` + `.json` with Top-25 Jaccard overlap, full-rank Spearman ρ, side-by-side validation verdicts, filtered EV/day (top quintile only, gated on validation positive), and a plain-English recommendation. **No auto-switch.** Flip `Engine_Champion` in `config.csv` manually when ready.

## Tests
- New `tests/test_new_modules.py` — 9 tests for regime, sector RS, microstructure, dq flags, orchestrator wiring. All pass.
- Existing `tests/test_core.py` — 14/14 still pass.
- Total: **23/23 passing offline** (no network).

## Files added
```
run_app.py
run_app.bat
run_app.command
orchestrator.py
dq_report_builder.py
shadow_vs_official_report.py
core/data_quality.py
core/regime.py
core/sector_context.py
core/etf_microstructure.py
tests/test_new_modules.py
CHANGES_v4_2.md
```

## Files patched
```
cross_sectional_validation.py   # writes validation_status.json
trade_plan_builder.py           # reads structured verdict, markdown is fallback
```

The existing `.bat` files still work — `orchestrator.py --all` reproduces the same sequence the old batch ran, and the GUI calls the same orchestrator. To use the new button:

```
pip install PySide6 pandas numpy yfinance
python run_app.py        # or double-click run_app.bat / run_app.command
```
