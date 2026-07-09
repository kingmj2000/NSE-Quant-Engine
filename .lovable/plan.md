
# Dashboard actionability refinement (revised)

Refinement pass on `desktop/nse_quant_engine/dashboard_html_builder.py` (plus one tiny append in `shadow_vs_official_report.py` to persist a run ledger, and one config constant). Keeps the black+crimson glassmorphic theme, glow system, and every honesty caption. No new computation in the dashboard layer, no LLM, no network. All new panels read existing `output/` files; missing files render "not yet available", never fabricated values.

## 1. New top section — "Progress to a verdict"

Injected right after the header, above **Market context**. One `.g-violet` glass row with three glanceable elements.

### 1a. Verdict chip (color-coded, plain-English gloss)

Verdict source, in order:
1. `output/validation_status.json → verdict`
2. If missing/unreadable → parse `output/cross_sectional_validation_report.md` for one of the known verdict strings from `core/validation_status.py::VALID_VERDICTS` (first exact match on a line that starts with a verdict heading like `**Verdict:**`).
3. If neither exists → neutral chip "Verdict not yet available" with gloss "Validation has not been run in this output directory." **Never** default to a positive verdict.

Gloss table (keyed off verdict string):
- `Validation Positive` → "Edge confirmed — live mode."
- `Insufficient History` / `Insufficient Independent History` / `Insufficient Statistical Evidence` / `Insufficient Breadth` → "Not enough evidence yet — watchlist only."
- `No Proven Edge Yet` → "No measurable edge after costs yet — watchlist only."
- `Validation Negative` → "Edge is negative after costs — do not act on picks."
- unknown / missing → "Verdict not yet available."

Chip color: green for Positive, red for Negative, amber for the "insufficient / no proven edge" family, neutral (blue-grey) for missing/unknown.

### 1b. Progress bar — verdict-gate readiness

**Primary track** is `effective_validation_dates` toward `CROSSVAL_MIN_EFFECTIVE_DATES` (the verdict gate). Rendered as the existing `.rbar` with label like "17 / 25 effective dates" (values pulled from `stats` and `core.config`).

**Secondary marker** on the same bar, drawn as a small labeled tick at the position `ADAPTIVE_MIN_DATES / CROSSVAL_MIN_EFFECTIVE_DATES` (clamped to ≤ 100%), captioned "adaptive-weighting readiness (60)". Explicitly kept visually separate so the two thresholds are never conflated — the primary bar fills toward the verdict gate; the tick only marks where the shadow adaptive layer would become eligible.

A one-line under-caption: "Verdict requires ≥ `CROSSVAL_MIN_EFFECTIVE_DATES` effective dates plus spread, t-stat, and bootstrap gates. Adaptive weighting is a separate downstream gate — see tick."

Below the bar, a secondary line shows raw `validation_dates / CROSSVAL_MIN_DATES` in small text ("41 / 30 raw dates — clears breadth floor") so both counts stay visible without visually competing.

### 1c. Maturation summary + delta chip

- Count strip: "Matured **X** · Awaiting **Y** · Total **Z** (10-day slice)" — reuses the numbers already in `maturity`.
- Delta chip logic:
  - Read `output/score_history.csv`. Group by `Date`.
  - Find the most-recent distinct date **strictly earlier than today's run date** (today = `datetime.now().date()` as string in the same format the history uses).
  - Compute `delta_matured = matured_today − matured_that_prior_date`.
  - Render chip: `▲ +N` (teal) / `▼ −N` (amber) / `flat` (dim).
  - **Hide the chip entirely** when there is no strictly-prior distinct date (i.e. only same-calendar-day history exists, or history is empty/missing). No placeholder, no zero-delta.

The existing "N signals maturing" pill inside the lower verdict banner is removed to avoid duplication.

### Payload additions in `_payload()`

- `progress.verdict`, `progress.gloss`, `progress.state` (`green` / `amber` / `red` / `neutral`), `progress.source` (`validation_status.json` / `cross_sectional_validation_report.md` / `unavailable`)
- `progress.effective_now`, `progress.effective_target` (= `CROSSVAL_MIN_EFFECTIVE_DATES`), `progress.adaptive_tick` (= `ADAPTIVE_MIN_DATES`), `progress.raw_now`, `progress.raw_target` (= `CROSSVAL_MIN_DATES`)
- `progress.matured`, `progress.maturing`, `progress.total`, `progress.delta_matured` (null when hidden)

Config thresholds pulled via `getattr(C, name, default)` so a missing symbol never crashes the build.

## 2. Alpha Zoo → evidence panel

Replaces the current survivor / top-by-IC block. Reads:
- `output/alpha_promotion_log.json` — per candidate: standalone IC, residual IC, t-stat, promote/reject, reason
- `output/alpha_zoo_ic_report.csv` — mean_IC, tstat, n windows for alphas already in the zoo
- `output/alpha_zoo_survivors.json` — survivor flag + thresholds (already loaded)

Rendered as a compact table inside the existing glass panel. Columns: **Alpha · Standalone IC · Residual IC · t-stat · Windows · Verdict**. Verdict cell is a chip:
- green "Promoted"
- amber "Watch — below residual gate"
- red "Rejected"
- dim "Baseline (no eval yet)"

`delivery_momentum` and `iv_rank` rows get a `.lblchip.review` "New candidate" tag next to the name so they stand out. Caption above the table: "Survivor gate: IC ≥ `min_ic`, t-stat ≥ `min_tstat`, residual IC ≥ `ALPHA_INCREMENTAL_IC_MIN`. New candidates enter live scoring only after clearing all three."

Panel hides (as today) when none of the three source files exist.

## 3. Shadow vs Official → running record

The four KPI tiles (overlap / added / dropped / regime chip) stay. A **streak strip** is added above the horizontal stacked bar.

### Ledger (written by the report script, read by the dashboard)

Append-only `output/shadow_vs_official_history.csv` with columns:
`date, verdict, shadow_state, shadow_beats_official_net, shadow_matured_obs, overlap`.

Written by `shadow_vs_official_report.py` after it computes today's record, dedup by `date` (overwrite the row if today already exists — idempotent per calendar day). Fail-soft. The dashboard remains read-only.

### Dashboard reads the last ~30 rows and computes

- `consecutive_shadow_leads` — trailing run of `shadow_beats_official_net == True`
- `consecutive_verdict_positive` — trailing run of `verdict == "Validation Positive"`
- `latest_shadow_matured_obs` — from the newest row

### Streak strip

Three inline pills:
- "Shadow lead streak: **N runs**"
- "Verdict-positive streak: **N runs**"
- "Green requires: ≥ `SHADOW_GREEN_MIN_STREAK` (8) consecutive leads **AND** verdict = Validation Positive **AND** shadow matured-independent obs ≥ `SHADOW_GREEN_MIN_MATURED_OBS` (default = `CROSSVAL_MIN_EFFECTIVE_DATES`, i.e. the standard floor)."

### Tightened `shadow_state` logic in `_payload()`

`shadow_state = "green"` **only if all four** hold:
1. `verdict == "Validation Positive"`
2. `consecutive_shadow_leads >= SHADOW_GREEN_MIN_STREAK`
3. `consecutive_verdict_positive >= SHADOW_GREEN_MIN_STREAK`
4. `latest_shadow_matured_obs >= SHADOW_GREEN_MIN_MATURED_OBS`

Otherwise `red` when the report recommendation explicitly says "do not switch" / "official still leads" / verdict is Negative, else `amber`. The amber reason surfaces which of the four checks fell short (e.g. "Only 2 consecutive leads — 8 required"), so a one-day lead visibly cannot flip to green. This must not be easier to earn than the documented six shadow-switch criteria.

### Config additions (in `core/config.py`)

- `SHADOW_GREEN_MIN_STREAK = 8`
- `SHADOW_GREEN_MIN_MATURED_OBS = CROSSVAL_MIN_EFFECTIVE_DATES` (or the numeric default if that constant is absent)

Both read via `getattr` with the defaults above so missing symbols never crash.

## 4. Watchlist-only visual dominance on Top-5 cards

While `verdict != "Validation Positive"`:

- Persistent full-width amber-bordered banner directly above `#cards`: "Reference levels only — not validated. Buy zones, stops, and targets below are mechanical outputs, not recommendations." Auto-hidden when verdict is positive.
- Per-card diagonal ribbon in the top-right corner (CSS-only): amber "WATCHLIST" on non-positive verdict, teal "LIVE" on positive. Replaces the current small `.lblchip` label for that state (no double-badging). "Also in shadow Top 5" chip stays.
- `Model_Edge_%_Per_Day` continues to render as `—` when blank, and the existing per-day / model-edge caption underneath the Shadow Unique block is preserved verbatim.

## Clutter reduction

- **Universe composition**: donut + `<h2>` removed. Replaced by a small pill-strip in the header sub-line: `Nifty50 · 50 | Next50 · 50 | Midcap150 · 150 | ETF · N` from the existing `universe_counts` payload.
- **Timing filter map (RSI vs volatility scatter)**: wrapped in a `<details>` toggle "Show RSI × volatility map", collapsed by default while verdict is not positive, auto-`open` when positive. Chart and payload unchanged.

## Preserved (unchanged)

Glassmorphic theme + glow system, embedded Chart.js, `.bottomline` framing, "cross-sectional report is the authority" sub-header, every honesty caption (per-day ceiling, medians-not-means, veto, model-edge caption), governance-veto behavior, ETF gaps section, Excel-ready summary line, data-provenance sub-lines.

## Files changed

- `desktop/nse_quant_engine/dashboard_html_builder.py` — payload additions (`progress`, `alpha_evidence`, `shadow_history`, tightened `shadow_state`), new Progress-to-a-verdict section, rewritten Alpha Zoo panel, streak strip in shadow row, watchlist banner + card ribbon CSS + template, universe donut → header strip, scatter section wrapped in `<details>`.
- `desktop/nse_quant_engine/shadow_vs_official_report.py` — append today's record to `output/shadow_vs_official_history.csv` (dedup by date). Fail-soft.
- `desktop/nse_quant_engine/core/config.py` — add `SHADOW_GREEN_MIN_STREAK = 8` and `SHADOW_GREEN_MIN_MATURED_OBS` (defaulting to `CROSSVAL_MIN_EFFECTIVE_DATES`) with a comment tying them to the documented six shadow-switch criteria.

## Verification

Render `dashboard_latest.html` from the current output directory (verdict = Insufficient History) and confirm:
- Progress section shows amber verdict chip with the correct gloss, primary bar fills toward `CROSSVAL_MIN_EFFECTIVE_DATES` with the ADAPTIVE tick clearly labelled and visually separate.
- Maturation delta chip is hidden when only today's date exists in `score_history.csv`, appears with correct sign when a prior distinct date exists.
- Watchlist banner is visible; every card shows the amber WATCHLIST ribbon.
- Shadow chip is not green (streak 0 or 1); amber reason names which of the four green-gate checks fell short.
- Alpha Zoo table shows delivery_momentum / iv_rank tagged as new candidates when the promotion log has them; panel says "not yet available" if all three source files are absent.

Then delete each of `validation_status.json`, `cross_sectional_validation_report.md`, `alpha_promotion_log.json`, `shadow_vs_official_history.csv`, `score_history.csv` in turn and reconfirm:
- Missing both validation sources → neutral "Verdict not yet available" chip, never a default-positive.
- Missing history → delta chip hidden and shadow streak pills read "0 runs".
- Missing alpha files → Alpha Zoo panel says "not yet available".
- No crashes, no fabricated values.
