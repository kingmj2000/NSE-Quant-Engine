## Fix dashboard_html_builder crash + layout/quintile issues

### Root causes

1. **`AttributeError: 'float' object has no attribute 'get'`**
   In `dashboard_html_builder.py` the quintile-loop (lines ~190-197) uses a local variable named `val` inside the `for label in (...)` loop. This shadows the outer `val` (the validation JSON dict loaded earlier around line 100). Later, at line ~390, the builder does `val.get("regime")` — by then `val` has been reassigned to a float (or `None`) by the loop, so it crashes. Because the builder crashes, the dashboard HTML is never regenerated → the app displays the last successful (stale) build with the wrong layout / blank quintile.

2. **Pie charts not side-by-side**
   The `.twocol` CSS collapses to one column below `max-width: 900px`. The embedded WebView renders at a viewport narrower than 900px, so both pies stack. Fix: lower the breakpoint (e.g. 640px) so the two-column layout survives the WebView width.

3. **Quintile chart blank**
   `qvals` contains numeric values very close to zero (early accumulation), so Chart.js draws axes with no visible bars. Strengthen the empty-state check to also trigger when every value is effectively zero (`|v| < 1e-3`), so the "awaiting matured horizon" message shows instead of an empty chart.

### Changes (single file: `desktop/nse_quant_engine/dashboard_html_builder.py`)

1. Rename the loop variable in the quintile aggregation block from `val` → `q_val` (and update the two references in that block). No other logic changes.
2. Lower the `.twocol` media-query breakpoint from `max-width:900px` to `max-width:640px`.
3. In the quintile JS block, change the empty-state guard to also treat all-near-zero values as empty:
   `if(!qvals.length || qvals.every(v => v===null||v===undefined||Math.abs(Number(v))<1e-3))`.

### Validation

- `python -m py_compile dashboard_html_builder.py`
- Run `python dashboard_html_builder.py` against existing `output/` artifacts to confirm it now exits `ok` and produces `output/dashboard.html`.
- Grep the produced HTML to confirm the twocol section and the quintile empty-state message are present.

No other files are touched. This is a targeted fix for the crash and the two visible layout defects.