## Goal
Surface the Step 8 + 10–16 artifacts in the desktop UI so the "Run" button visibly reflects the new pipeline, without changing any analytics logic.

## What already works (no change needed)
`run_app.py → orchestrator.py → trade_plan_builder.py` already calls every new module. Verified references in `trade_plan_builder.py`:
- Step 8 `position_sizer`, Step 10 `backtest_engine`, `fundamentals_overlay`
- Step 12–13 `sector_context`, `event_calendar`, `expected_value`, `portfolio_validation`
- Step 14–16 `institutional_flow`, `regime_tilt`, `rebalance_diff`
- `evidence_bundle` (packs everything + prompt into the zip)

Artifacts land in `output/` and in the zip. The run button is not broken.

## What is missing — UI only
Existing tabs: Dashboard, Scores, Shadow, Compare, DQ Report, Validation, Trade Plan. New artifacts only appear inside `trade_plan_report.md` / the zip; there is no dedicated panel.

## Proposed changes (UI only, `run_app.py`)

### 1. New "Portfolio" tab
Single scrollable page with 4 cards driven by existing CSV/JSON outputs:
- **Sizing** — table from `top5_sizing.csv` (Weight%, Capital, Stop, Max Loss, Risk Contribution).
- **Sector & Peers** — table from `top5_sector_context.csv`.
- **Events & EV** — merged table of `top5_event_calendar.csv` + `top5_expected_value.csv` with an amber pill when `Event_Risk_Flag = In_Window`.
- **Portfolio Validation** — verdict pill (green/amber/red) + reasons list from `portfolio_validation.json`.

### 2. New "Macro & Rotation" tab
Three cards:
- **Institutional Flow** — FII regime pill + `top5_institutional_flow.csv` table.
- **Regime Tilt** — regime pill + family-multiplier chips from `regime_tilt_report.json` (report-only badge when `applied_to_scoring=false`).
- **Rebalance Diff** — Holds / Exits / Entries chips, turnover %, net-edge-after-cost, recommendation pill from `rebalance_diff.json`. First run shows "First run — establishing positions".

### 3. Dashboard KPI strip additions
Append three tiles reading the same JSONs already produced:
- Batch Verdict (from `portfolio_validation.json`)
- Turnover % (from `rebalance_diff.json`)
- FII Regime (from `macro_context.json`, already exists on disk)

### 4. Evidence-bundle button
Add a Ghost button next to the run controls: "Open Evidence Zip" — opens the newest `output/evidence_bundle_*.zip` in the OS file browser. This is the artifact the user hands to Claude.

## Out of scope
- No changes to analytics, module logic, or orchestrator.
- No new Python dependencies. All new panels reuse the existing `_df_to_model`, `Card`, and pill styles.
- No changes to `run_app.bat` — same one-button launch.

## Files to touch
- `desktop/nse_quant_engine/run_app.py` — add `PortfolioView`, `MacroRotationView`, extend Dashboard KPI strip, add "Open Evidence Zip" button, register two new tabs.

## How to run after this change
Unchanged — double-click `run_app.bat` (Windows) or `run_app.command` (macOS), click the run button. The two new tabs populate as soon as the pipeline finishes.