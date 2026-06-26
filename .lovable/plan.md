## Plan

### 1. Fix the stuck pipeline step
- Inspect `orchestrator.py`, `run_app.py`, and the ETF AUM fetcher script.
- Find why `etf_aum_auto_fetcher` shows `done ... [pending]` and then leaves the app in `Running...`.
- Add a proper process timeout / completion signal so skipped or completed steps cannot leave the UI hanging.
- Make the runner print a clear final state: `Completed`, `Failed`, or `Timed out`, instead of staying silent.

### 2. Separate real data gaps from fixable data-quality problems
- Update the ETF data-quality report so expected non-coverage is classified clearly:
  - `Tracking error filled: 0 / 328` should be treated as a source-disclosure limitation if NSE/AMFI do not publish it reliably.
  - Tracking difference / NAV / benchmark / AUM / TER should be evaluated separately.
- Improve the terminal and UI summary so it says which gaps are actionable versus structurally unavailable.
- Keep manual review files for the few genuinely unresolved mappings or missing metadata.

### 3. Improve ETF metadata and quality logic
- Review `etf_metadata_enricher.py`, `etf_quality_builder.py`, and `core/data_quality.py`.
- Ensure joins prioritize stable identifiers first where available, then name-based matching only as fallback.
- Avoid treating absent tracking error as a fatal quality problem when tracking difference is available.
- Make quality flags less noisy and more decision-useful.

### 4. Guarantee normal run completes before shadow run
- Update orchestration so the shadow workflow starts only after:
  - universe build completes,
  - metadata enrichment completes,
  - ETF quality build completes,
  - normal scoring output exists and passes basic sanity checks.
- If normal output is incomplete or stale, skip shadow and show a clear warning.
- Prevent parallel shadow execution from reusing half-written data files.

### 5. Modernize the desktop UI
- Redesign the PySide6 app with a black / charcoal base, red accent highlights, rounded panels, and subtle glassmorphic styling.
- Improve the top controls and run-state display so it is obvious which stage is running.
- Replace the raw report-first feel with a dashboard view for the latest run.

### 6. Add dashboard summaries and visualizations
- Add latest-run cards for:
  - data coverage: NAV, AUM, TER, benchmark, tracking proxy,
  - official vs shadow status,
  - validation verdict,
  - data-quality health,
  - top candidate counts.
- Add simple charts/tables in the desktop app using available local CSV/JSON outputs.
- Keep the existing raw tabs for `Scores`, `Shadow`, `Compare`, `DQ Report`, `Validation`, and `Trade Plan`, but make the dashboard the first view.

### 7. Validate and repackage
- Run offline smoke tests/import checks for the updated scripts.
- Run the existing unit tests.
- Rebuild the downloadable zip with the fixed codebase and quickstart files.

## Expected outcome
- The app should no longer get stuck after the AUM fetcher step.
- Data-quality output should explain that some tracking coverage gaps are real source limitations, not code failures.
- Shadow mode should never run before normal mode has completed usable outputs.
- The desktop app should feel more modern and include an actual latest-run dashboard instead of only raw text reports.