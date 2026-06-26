
## Goals

1. Reopening the desktop app should immediately show the most recent dashboard, scores, and run metadata — no blank state, no need to re-run.
2. Tone the theme down: red becomes one accent among several (deep indigo/violet base, teal/amber/green for status), with the glassmorphic surfaces kept.
3. Move the noisy live "steps + log" strip out of the main viewport into a collapsible side surface, so the dashboard owns the screen.

Nothing about scoring, validation, shadow, or DQ logic changes — this is GUI + persistence only.

## 1. Persistent last-run state

**On launch (`run_app.py` startup):**
- Read `output/run_manifest.json` (new, see below) if it exists.
- Populate header: "Last run: 2026-06-25 18:42 · Official ✓ · Shadow ✓ · Champion: Official".
- Auto-load `output/dashboard_latest.html` into the Dashboard `QWebEngineView`.
- Auto-load the latest CSVs into the Scores / Shadow / Compare / DQ / Validation / Trade Plan tabs (same loaders already used at end-of-run, just lifted into a `load_last_run()` helper called both at startup and at run completion).
- If no artifacts exist yet, show a single empty-state card: "No run yet — click Run to generate today's dashboard."

**On run completion:**
- Orchestrator writes/updates `output/run_manifest.json`:
  ```json
  {
    "completed_at": "...",
    "official_status": "ok|partial|failed",
    "shadow_status": "ok|partial|skipped|failed",
    "champion": "official|shadow",
    "artifacts": {"dashboard_html": "...", "scores_csv": "...", ...}
  }
  ```
- GUI re-runs `load_last_run()` → dashboard `reload()`, tabs refresh.

**Refresh button** in the dashboard toolbar: calls `load_last_run()` again — picks up any new artifacts on disk (useful if the user regenerated via CLI or edited a file).

## 2. Reduced-red glassmorphic theme

Replace the current red-dominant palette in both the QSS (`run_app.py`) and the dashboard CSS variables (`dashboard_html_builder.py`) so they stay in lockstep:

```text
--bg            #0A0B12       deep ink
--bg-grad       linear-gradient(135deg,#0A0B12 0%,#10131F 50%,#0E0A18 100%)
--panel         rgba(22,24,34,0.62) + backdrop-filter blur(22px)
--panel-2       rgba(28,30,42,0.45)
--line          rgba(255,255,255,0.07)
--txt           #ECEDEE
--muted         #8A92A6

--accent        #6E8BFF   (indigo)   ← new primary accent (was red)
--accent-2      #8E7BFF   (violet)
--teal          #38BDB0   positive / champion
--amber         #F2B13C   caution / watchlist
--green         #3FB950   validated
--red           #E5556A   reserved for veto / critical only
--gradient-hero linear-gradient(135deg,#6E8BFF 0%,#8E7BFF 55%,#38BDB0 100%)
--gradient-warn linear-gradient(135deg,#F2B13C 0%,#E5556A 100%)
--glow          0 10px 40px -12px rgba(110,139,255,.35)
```

Usage rules:
- Verdict banner uses `--gradient-hero` for "Validation Positive", `--gradient-warn` for "Watchlist Only / Insufficient", solid `--red` only for hard veto.
- Primary buttons, active tab, KPI numbers → indigo/violet gradient.
- Champion chip, "switch to shadow" recommendation → teal.
- Governance veto rows, error toasts → red (kept, just no longer the dominant hue).

## 3. Run log moved out of the main view

Replace the top step strip + console pane with a **collapsible right-side Run Drawer**:

```text
┌──────────────────────────────────────────────┬─────────────┐
│  Tabs: Dashboard | Scores | Shadow | ...     │  ▸ Run      │
│                                              │   Drawer    │
│  (Dashboard fills the screen)                │  (collapsed)│
└──────────────────────────────────────────────┴─────────────┘
```

- Default state: collapsed to a 36 px rail showing only a vertical "RUN" label, a live status dot (idle/running/ok/fail), and a small progress ring (e.g. 7/12 steps).
- Click the rail → drawer slides out to ~340 px:
  - Top: status header ("Running step 7 of 12: cross_sectional_validation · 00:42 elapsed").
  - Middle: condensed step list (✓ done, ● current with spinner, ○ pending). Hovering a step shows its duration; clicking a failed step jumps the log to that section.
  - Bottom: tail of `run.log` (auto-scroll, monospace, ~10 lines visible, "Open full log" link).
- While idle the drawer header shows the manifest summary ("Last run 18:42 · 12/12 ok"), so the user can always see *something* without opening it.
- The big "Run" button moves into the dashboard top bar next to the date / refresh / "Open in browser" buttons — it's the primary CTA and shouldn't be hidden in the drawer.

This keeps the dashboard uncluttered during normal use, but the full pipeline detail is always one click away and never blocks the visual.

## Files touched

- `orchestrator.py` — write `output/run_manifest.json` after each run; emit a `step_done` event with duration so the drawer can render the step list.
- `run_app.py` — `load_last_run()` helper, startup auto-load, refresh button, new QSS palette, new `RunDrawer` widget (`QDockWidget` on the right, collapsible), removal of the top step strip + bottom log pane.
- `dashboard_html_builder.py` — swap CSS variables to the new palette, update banner / chip / gradient classes; layout unchanged.
- `requirements.txt` / `setup_windows.bat` — unchanged.

## Out of scope

- Scoring, validation, shadow, DQ logic.
- Any non-Windows packaging.
- Live-streaming a remote run — the drawer reads local events only, same as today.

## Deliverable

Repackaged `nse_quant_engine_v4_4_patched.zip`: launch the app cold → last run's dashboard is already on screen in the new indigo/violet glassmorphic theme; the right-side rail shows "Last run ✓ 12/12"; clicking Run kicks off a fresh pipeline whose progress lives in the drawer, and when it finishes the dashboard reloads in place.
