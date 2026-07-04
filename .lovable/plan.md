## Plan

1. **Replace red-heavy dashboard visuals**
   - Remove the matured/maturing donut entirely.
   - Add clean metric cards: Matured, Awaiting maturation, Total signals, Maturation rate.
   - Compute counts from the full `forward_return_history.csv`, filtered to the 10-day horizon when a horizon column exists so numbers reflect the actual dataset slice.

2. **Replace the 5-day vs 10-day evidence field card**
   - Remove the current thin/error-styled evidence tile block.
   - Replace with a Validation Readiness visual: validation breadth, effective sample size, top-vs-bottom spread, adjusted t-stat, bootstrap confidence.
   - Status colors: teal (usable), blue/violet (building), amber (thin) — no red.

3. **Dashboard color overhaul**
   - Reserve crimson strictly for primary CTA, tabs, header pill, and a few key note headers.
   - Remove red glow, red borders, and red fills from candidate cards, chart palettes, risk chips, avoid rows, per-day tiles, stop-loss numbers, and shadow blocks.
   - Modern dark glassmorphic trading palette:
     - Teal / green for positive
     - Amber / coral for caution
     - Blue / violet for neutral / structural
     - Muted slate for unknown
   - Repaint scatter dots, quintile bars, universe donut, shadow bar, verdict banners, and stop level accordingly.

4. **Fix the dark row rectangles inside Trade Plan and Validation cards**
   - The dark bars are from `QTableView` / `QPlainTextEdit` / `QTextBrowser` and inner mini-cells rendering opaque dark rectangles inside translucent glass cards.
   - Make inner value cells, tables, and text panels transparent with subtle glass styling:
     - Remove solid backgrounds on table backgrounds, row cells, and inner `.lv` / `.pd` mini-cells.
     - Replace with `background: transparent` or `rgba(...,0.04)` and remove hard borders.
     - Consistent 8–10px radius, no visible row separators inside cards.
   - Apply the same clean-up to md_to_widgets tables, DQ tab tables, and Trade Plan card level rows.

5. **Clean up the scatter plot**
   - Remove axis grid and tick labels.
   - Keep clean plotting area, subtle bands optional, and rich hover tooltip with symbol, RSI, and volatility.
   - Non-red dot colors: teal for clean, amber for caution.

6. **Fix Windows auto-close / invisible terminal output**
   - Harden `run_app.py` so any startup or shutdown exception surfaces a persistent error dialog and a written crash log under `output/last_crash.log` before the process exits.
   - Explicitly set the app not to quit implicitly on transient WebEngine close; ignore stray quit calls while a run is active.
   - Update `run_app.bat` to keep the console open on both success and failure, print the exit code, and use `cmd /k pause` semantics so users can read errors regardless of how the app exited.

7. **Validation after changes**
   - Syntax-check every changed Python file.
   - Regenerate dashboard HTML if data is present; inspect for removed donut, new metric cards, non-red palette, gridless scatter, and transparent inner rows.
   - Confirm no remaining red borders/glow outside primary CTA/tabs/header.

## Files to change

- `desktop/nse_quant_engine/dashboard_html_builder.py`
- `desktop/nse_quant_engine/run_app.py`
- `desktop/nse_quant_engine/md_to_widgets.py`
- `desktop/nse_quant_engine/run_app.bat`

No web frontend files are changed.