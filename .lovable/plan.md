## Plan

### 1. Fix charts inside the Python app window
- Remove the dashboard’s dependency on loading Chart.js from the internet CDN.
- Bundle or inline the Chart.js runtime directly into the generated `dashboard_latest.html`, so the same HTML works inside `QWebEngineView` and in a normal browser.
- Add a safe dashboard script guard: if chart rendering fails, show a clean inline chart error message in that chart panel instead of breaking the whole dashboard.
- Keep the browser version working exactly the same.

### 2. Stop the app window from closing on end-of-run errors
- Wrap the runner thread execution in top-level exception handling.
- Emit failures back to the GUI drawer/status bar instead of allowing an unhandled exception to terminate the Python process.
- Add a `QWebEngineView` JavaScript console hook if available, so errors like `Chart is not defined` appear in the Activity drawer/log rather than silently failing.
- Keep the app open after a failed pipeline step, with the last successful dashboard still visible.

### 3. Improve dashboard styling with crimson as a controlled primary accent
- Adjust the current indigo/violet-heavy palette to a clean black glassmorphic theme with crimson/red as the primary CTA/header accent, but not a danger-heavy red wash.
- Use crimson for primary buttons, active tabs, key header highlights, and selected KPI accents.
- Keep teal/green/amber for positive/caution/status semantics so the dashboard still reads calmly.
- Remove duplicate or overly noisy labels where charts already explain the data.

### 4. Fix ETF metadata / quality coverage logic
- Update metadata coalescing so tracking difference is treated as a valid tracking quality metric when tracking error is unavailable.
- Split flags more accurately:
  - `Tracking error unavailable` when TE itself is missing.
  - `Tracking difference available` as a usable fallback, not a failure.
  - `Tracking quality metric missing` only when both TE and tracking difference are missing.
- Improve TER/AUM/benchmark matching by strengthening scheme-name normalization and alias matching across AMFI/import/manual data.
- Regenerate the unresolved review files so the remaining gaps are actionable, not duplicated false flags.

Important note: true 100% tracking-error coverage may not be possible if fund houses do not disclose it for every ETF. The fix will maximize usable coverage and avoid incorrectly treating tracking-difference rows as missing quality data.

### 5. Expand stock universe beyond Nifty 50 + Nifty Next 50
- Extend `universe_builder.py` to include a broader equity universe, preferably Nifty Midcap 150 / Nifty 200-compatible symbols depending on what can be fetched reliably.
- Add the expanded stock rows into `config.csv` with a distinct `Universe_Group` such as `NiftyMidcap150` or `Nifty200Extra`.
- Preserve the existing ETF universe and existing scoring/validation logic.
- Make the universe builder tolerant of missing NSE source downloads by reusing cached files or existing config rows.

### 6. Verify and package
- Run the relevant Python modules locally enough to confirm:
  - `dashboard_latest.html` contains no external Chart.js dependency.
  - no `Chart is not defined` can occur in the embedded app.
  - metadata flags no longer misclassify tracking-difference rows.
  - the expanded universe is included without breaking price downloads.
- Repackage a new zip, likely `nse_quant_engine_v4_6_patched.zip`, for download.

## Files expected to change
- `dashboard_html_builder.py`
- `run_app.py`
- `universe_builder.py`
- `etf_metadata_enricher.py`
- `etf_quality_builder.py`
- possibly `config.csv` / cached universe files
- packaged output zip