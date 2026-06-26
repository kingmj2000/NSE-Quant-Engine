## Goal

Replace the current widget-based Dashboard tab with a fully styled HTML evidence-review dashboard — same layout and information density as `nse_quant_dashboard_2026-06-25.html`, re-skinned to the existing black + red glassmorphic theme. The same HTML is also written to disk (`output/dashboard_<YYYY-MM-DD>.html`) so it can be shared, archived, or opened in a browser independently of the app.

## What the dashboard will show (matches the reference, driven by real artifacts)

| Section | Source |
|---|---|
| Bottom-line banner (Watchlist / Live / Paper) | `validation_status.json` + `shadow_vs_official.json` |
| Validation verdict card + pills (signal count, regime, mode) | `validation_status.json`, `core/regime.py` output, run_log |
| Forward-window maturity donut | `forward_return_history.csv` (matured vs maturing) |
| 5 evidence tiles (Val. Dates, Eff. Dates, Q-spread, t-stat, bootstrap) | `cross_sectional_validation_detail.csv` / `validation_status.json` |
| Quintile median net-return bar (5D + 10D toggle) | `score_bucket_performance.csv` |
| Shadow Model chip + reason + overlap pills | `shadow_vs_official.json` + diff of top-N |
| Top-N watchlist cards (price, buy zone, stop, T1/T2, hold, %/day, flags) | `trade_plan_latest.csv` + `latest_scores.csv` (RSI/vol/news flags) |
| RSI vs 20-day vol scatter with overbought / hi-vol bands | `latest_scores.csv` |
| Avoid / downgrade table (governance veto, RSI, vol) | `trade_plan_latest.csv` + governance veto list in `core/config.py` |
| Shadow-only names to watch (in shadow Top-N, not official) | diff of `latest_scores.csv` vs `latest_scores_v4_shadow.csv` |
| ETF gaps & data-quality notes | `dq_summary.json` + `etf_quality_latest.csv` |
| Excel-ready one-line summary footer | composed from above |

## Files

**New `dashboard_html_builder.py`** (top level, project-relative).
- `build(date=None) -> Path` reads all artifacts and renders one self-contained HTML file using a template string.
- Chart.js pulled from CDN, exactly as in the reference (no Python chart dep added).
- Output paths:
  - `output/dashboard_latest.html` (always overwritten — used by the desktop app)
  - `output/dashboard_<YYYY-MM-DD>.html` (dated archive)
- All data serialized as a single `const DATA = {...}` JSON block, so the renderer JS in the template stays static and the HTML is a clean snapshot of that day's run.

**Theme (black + red glassmorphic)** — CSS variables aligned with the desktop app's QSS:
```text
--bg #0B0B0F     --panel rgba(24,24,30,0.72) with backdrop-filter blur(18px)
--panel2 rgba(20,20,26,0.55)    --line rgba(255,255,255,0.06)
--red #E53946    --red-soft #FF6B78    --red-bg rgba(229,57,70,0.15)
--amber #F2B13C  --green #3FB950       --blue #58A6FF  --violet #A371F7
--txt #ECEDEE    --muted #9BA1A6
```
- Rounded 14–18px corners, gradient header (`#E53946 → #7A0E16`) on the verdict chip and primary pills, subtle red glow shadow on cards (`0 10px 40px -10px rgba(229,57,70,.35)`).
- Verdict banner colour is conditional: Validation Positive → green tint, Insufficient/Negative → red tint, Watchlist → amber-red mix. So "Watchlist Only" reads bold red instead of amber as in the reference.
- Cards use the existing `.tile / .card / .lv / .pd` structure from the reference for visual parity; only the palette and glass treatment change.

**`orchestrator.py`** — add a final step:
```text
Step("dashboard_html_builder", _module("dashboard_html_builder"))
```
Runs after `shadow_vs_official_report` so all inputs exist. Gated on `latest_scores.csv` existing (same gate already in use).

**`run_app.py`** — Dashboard tab changes:
- Replace the hand-built `Dashboard` widget with a `QWebEngineView` that loads `output/dashboard_latest.html`.
- Reload the page automatically when the runner thread emits a step event whose name contains "dashboard" (live refresh at the end of the run).
- Keep all other tabs (Scores, Shadow, Compare, DQ, Validation, Trade Plan) unchanged.
- Add an "Open in browser" button in the dashboard tab toolbar that does `webbrowser.open(file_url)`.
- If `PySide6.QtWebEngineWidgets` is not installed, the tab falls back to a "View report in browser" button (no hard crash). `requirements.txt` and `setup_windows.bat` will install `PySide6` (which bundles QtWebEngine on Windows).

## Edge cases handled

- Missing artifact: each section renders a "(no data yet — run pipeline)" placeholder instead of breaking layout.
- Validation insufficient / no shadow: charts gracefully render with the partial data we have (e.g. donut shows only "maturing" slice).
- Adani / governance veto: pulled from `core/config.py` list, not hard-coded into the HTML.
- Shadow recommendation chip uses the `recommendation` text from `shadow_vs_official.json` so "switch to shadow" / "keep official" / "review" colours are driven by data, not strings in the dashboard.

## Out of scope (will not change)

- The scoring engines, validation logic, fetchers, and other tabs.
- The data-quality classification (already updated last turn).
- Any non-Windows packaging beyond what already exists.

## Deliverable

Same zip name pattern (`nse_quant_engine_v4_3_patched.zip`) with the new builder, orchestrator step, and updated GUI Dashboard tab. After running once, the user can either watch the dashboard inside the app or double-click `output/dashboard_latest.html` in a browser — both render the same evidence-review layout in the black + red glassmorphic theme.
