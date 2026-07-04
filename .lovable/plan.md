## Plan: NSE Quant Engine v4.7 patch

### 1. Stop the app window from auto-closing
- Root cause: `QWebEngineView` JS errors (like `Chart is not defined` earlier) and unhandled Qt signal exceptions still bubble to the process. `RunnerThread` catches Python errors but Qt/WebEngine crashes and `sys.excepthook` misses aren't trapped.
- Install a global `sys.excepthook` and `threading.excepthook` in `run_app.py` that pipes errors to the Activity drawer instead of terminating.
- Install a `QtMsgHandler` (`qInstallMessageHandler`) to catch Qt fatal/warn messages.
- Wrap `QWebEnginePage.javaScriptConsoleMessage` to log JS errors into the drawer (never crash).
- Add a `QApplication.aboutToQuit` guard: if quit fires without user action, cancel and log.
- Set `QApplication.setQuitOnLastWindowClosed(False)` on the runner window while a run is active, and only close on explicit user Quit action.

### 2. Fix colored glow per element (no global red glow)
- Current QSS uses one crimson `box-shadow`/`border` on every hover/focus.
- Refactor QSS + dashboard CSS so glow color inherits from element accent:
  - Primary buttons/headers → crimson glow.
  - Success/positive cards (green) → teal/green glow.
  - Info cards (blue) → cyan/blue glow.
  - Warning (amber) → amber glow.
- Implement via per-variant classes (`.card-success`, `.card-info`, `.card-warn`, `.card-primary`) with matching `--glow-color` CSS var and QSS object names (`setProperty("variant", ...)`).
- Remove red from the donut chart palette and from neutral card backgrounds. Reserve crimson for primary CTAs, active tab, and critical/veto states only.

### 3. Beautify DQ, Trade Plan and Validation report tabs
- Currently these tabs render CSV/MD as plain text.
- Replace with structured renderers:
  - **DQ Report tab**: KPI header cards (rows total, complete %, TER missing, AUM missing, tracking metric disclosed, benchmark filled) + a filterable `QTableView` styled as a glass table with colored status pills for each flag.
  - **Trade Plan tab**: summary strip (verdict, evidence grade, use-mode banner) + per-trade cards showing symbol, bucket, entry/stop/target, reason, key risk; toggle between card grid and table view.
  - **Validation Report tab**: parse the MD sections into collapsible glass panels (Verdict, Coverage, Spread Summary, Per-bucket stats) with inline mini-bars/sparklines instead of raw markdown.
- Add a small helper `md_to_widgets.py` to turn known section headings into styled panels.

### 4. Redesign dashboard visuals
- Replace **maturing vs matured donut** with two KPI count cards ("Matured signals", "Awaiting maturation") plus a horizontal stacked bar showing progress toward maturation across horizons (5D/21D/63D).
- Replace the **5-day vs 10-day donut** with a **Top-20 bucket distribution** chart (bars: Top Candidate / Candidate / High Potential but Risky / Watchlist) — a more decision-useful visual.
- Add a **Shadow vs Official** panel:
  - KPI: Top-20 overlap (9/20 from today's run).
  - Horizontal bar comparing shadow-only, official-only, common symbols.
  - List of neutralized factors from the shadow warnings block.
- Add a **Universe composition** donut (Nifty50 / Next50 / Midcap150 / ETF) sourced from the run manifest.
- Ensure no chart uses crimson as a category color (crimson reserved for the primary accent line/header only).

### 5. Enrich other tabs
- **Compare tab**: build a side-by-side table (Official vs Shadow) with columns Symbol, Official Rank, Shadow Rank, Δ Rank, Bucket change, Score delta, plus a scatter (Official Score vs Shadow Score) and a summary card (overlap %, avg |Δ rank|, top movers up/down).
- **News/Market tab**: card grid grouped by symbol with sentiment chip and source link.
- **Universe tab**: KPIs (total, opportunity-eligible %, per-group counts) + eligibility stacked bar.

### 6. Data-pipeline gaps surfaced by the terminal
- `cross_sectional_validation.py`: `validation_status.json write skipped: local variable 'spread_summary' referenced before assignment` → initialize `spread_summary = {}` (and any siblings) before the guarded branch so status JSON is always written; downstream tabs rely on it.
- `Tracking error disclosed: 0 / 328` — add a secondary fetcher pass: (a) NSE ETF factsheet scrape fallback, (b) fund-house AMC pages for top 20 AUM ETFs, (c) accept 1Y tracking difference from AMFI monthly disclosures when TE absent. Log per-symbol source in `etf_metadata_source_log.csv`.
- `Benchmark filled: 313 / 328` — add fuzzy match on ETF short-name → benchmark index dictionary for the 15 missing, and mark unresolved for manual mapping.
- Shadow-run warnings ("Price/MA trend columns missing", "Benchmark 21D return missing", "No fundamentals_latest.csv") — precompute price/MA trend + benchmark 21D return during main engine so shadow doesn't neutralize; add a stub `fundamentals_latest.csv` writer with the columns v4.1 shadow expects (even if values are blank) so the shadow run stops warning.
- Persist the coverage summary numbers into `run_manifest.json` so the DQ tab renders them without re-reading CSVs.

### 7. Package and validate
- Bump to `nse_quant_engine_v4_7_patched.zip`.
- Manual test checklist: open app cold → last run auto-loads → trigger a run → force a JS error in a chart → confirm drawer logs it and window stays open → confirm DQ / Trade Plan / Validation tabs render as cards/tables → confirm dashboard shows new bucket/shadow/universe visuals with per-variant glow colors.

### Files expected to change
- `run_app.py` (excepthooks, Qt message handler, JS console hook, per-variant QSS, new tab renderers, quit guard)
- `dashboard_html_builder.py` (new charts, per-variant glow tokens, remove red from categorical palettes)
- `cross_sectional_validation.py` (`spread_summary` init + always-write status JSON)
- `etf_metadata_enricher.py` (extra TE/TD fetchers, benchmark fuzzy match)
- `etf_quality_builder.py` (consume new sources, update flags)
- `orchestrator.py` (write coverage + shadow summary into `run_manifest.json`, emit `fundamentals_latest.csv` stub, precompute price/MA + benchmark 21D)
- New: `dq_view.py`, `trade_plan_view.py`, `validation_view.py`, `compare_view.py`, `md_to_widgets.py`
- Repackaged `nse_quant_engine_v4_7_patched.zip`

Note: full 100% tracking-error coverage remains bounded by AMC disclosure; the plan maximizes usable coverage and logs the reason per unresolved symbol.
