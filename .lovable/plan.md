## Plan

1. **Stop the post-pipeline auto-crash**
   - Replace the current in-process `runpy` pipeline execution inside the Qt app with an isolated child Python process.
   - Stream the child process output back into both the Activity drawer and the terminal, so you still see the full run log.
   - If the pipeline process crashes with `-1073741819`, keep the desktop UI open and show the crash as a failed child run instead of letting the whole app close.
   - Keep writing/reading `output/run_manifest.json` exactly as today so last-run loading remains compatible.

2. **Avoid the unsafe immediate full re-render after a run**
   - After the child pipeline completes, do not immediately rebuild every heavy tab in the same completion callback.
   - Update the run status, last-run pill, and native dashboard summary safely first.
   - Schedule a delayed safe refresh, with each tab guarded independently, so one report/table render cannot terminate the app.

3. **Dashboard layout updates**
   - Place **Universe composition** and **Shadow vs Official** side-by-side in one responsive two-column section on desktop, stacking only on narrow screens.
   - Keep the visuals compact so both panels fit naturally together.

4. **Dynamic quintile horizon**
   - Stop hardcoding “10-day” in the quintile section.
   - Detect the available horizon from `score_bucket_performance.csv` / validation artifacts and choose the highest horizon with usable median-return data.
   - Update the chart title, dataset label, subtitle, and displayed values dynamically, so it can show 5-day now and automatically move to 10/21-day when those outputs mature.
   - Add a clear empty-state message if no usable median data exists.

5. **Official + shadow Top 5 candidates**
   - Show the normal/official Top 5 candidate cards as the primary section.
   - Mark any official Top 5 card that also appears in the shadow Top 5 with a visible “Also in shadow Top 5” marker.
   - Add a new section below showing only shadow Top 5 names that are unique to shadow, if any.

6. **Timing Filter Map labels**
   - Keep the clean no-grid/no-tick scatter style.
   - Add explicit axis labels: X = `RSI(14)` and Y = `20-day volatility (%)`.
   - Keep hover tooltips showing symbol, RSI, and volatility.

7. **Validation**
   - Syntax-check the changed Python files.
   - Verify the run path no longer calls the pipeline in-process.
   - Verify the generated dashboard HTML contains the side-by-side section, dynamic horizon labels, official/shadow Top 5 sections, and timing-axis labels.