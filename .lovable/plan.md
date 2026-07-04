## Plan

1. **Stop the native auto-close crash**
   - Treat exit code `-1073741819` as a native Qt/WebEngine access violation, not a normal Python exception.
   - Stop auto-refreshing the embedded WebEngine dashboard during and immediately after pipeline steps.
   - Replace the default in-app dashboard view with a stable native Qt summary when running inside the desktop app, while keeping `output/dashboard_latest.html` available through **Open in browser** for the full Chart.js dashboard.
   - Enable Python fault logging at startup so native crashes write usable details to `output/last_crash.log` when Windows permits it.
   - Set `QApplication.setQuitOnLastWindowClosed(False)` and add explicit quit handling so transient WebEngine/page destruction cannot close the whole app.

2. **Make the pipeline finish without closing the app**
   - After `dashboard_html_builder` completes, keep the main window open, reload only the native tabs, and show a completion status.
   - Add defensive guards around final reload/render calls so a bad report/dashboard artifact logs to Activity instead of terminating the app.

3. **Remove dark row/rectangle artifacts across cards**
   - Fix the global QSS root rule so it does **not** paint every child `QWidget` dark.
   - Make card holders, scroll-area contents, table viewports, labels, and markdown report widgets transparent by default.
   - Remove the striped/opaque row fills in `QTableView`, markdown tables, and Trade Plan card value grids.
   - Keep only subtle borders and accent colors, not black bands behind every line.

4. **Increase report content height**
   - Change markdown report rendering so text panels expand to content instead of being capped around one visible line.
   - For Validation and Trade Plan reports, render sections as full-height panels inside the scroll area with larger minimum heights.
   - Ensure report tables get enough height for multiple rows and use the parent tab scroll, not tiny nested scrollbars.

5. **Files to update**
   - `desktop/nse_quant_engine/run_app.py`
   - `desktop/nse_quant_engine/md_to_widgets.py`
   - `desktop/nse_quant_engine/run_app.bat` if needed for improved native-crash logging

6. **Validation**
   - Syntax-check changed Python files.
   - Confirm the app can complete a run and remain open.
   - Confirm Validation and Trade Plan cards no longer show black row bands and reports show multiple lines/sections at usable height.