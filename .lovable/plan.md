# NSE Quant Engine v4.8 patch

The v4.7 zip already replaced the DQ / Validation / Trade Plan tabs with structured widgets, but several intended changes did not actually take effect in the built app (dashboard maturation donut still present, Compare tab still a single-row table, version banner still says "v4.6 · glassmorphic", cards have inner black rectangles, Report sections still render raw markdown). This plan is a follow-up patch that finishes those items and re-themes the visuals away from a red-only palette.

Since the plan touches Python + Qt + embedded HTML/CSS/JS only, the web preview shown in Lovable is unrelated — deliverable is a new `nse_quant_engine_v4_8_patched.zip` you overwrite on top of your working folder.

## 1. App window: version + header

- Change the title bar / header pill from `v4.6 · glassmorphic` to just `v4.8` (single label, no descriptor). Update in `run_app.py` (`APP_VERSION = "4.8"`) and in the header widget that currently reads `f"v{APP_VERSION} · glassmorphic"`.
- Also update the window title (`setWindowTitle`) and About dialog string to match.

## 2. Dashboard redesign (embedded HTML — `dashboard_html_builder.py`)

Remove the two remaining donuts described in the screenshots and replace with decision-useful visuals. Charts stop using crimson as a categorical color; crimson is reserved for the primary tab/header/CTA gradient only.

- **Signal maturity block** (currently: pink donut + 5 empty "Thin" mini-cards):
  - Replace donut with 2 KPI cards: `Matured signals`, `Awaiting maturation` (teal for matured, amber for awaiting).
  - Below the KPIs, a horizontal stacked bar broken down by horizon (5D / 21D / 63D) showing matured vs pending per horizon.
  - Remove the 5 empty evidence-field cards when the underlying values are `—`; render one compact "Evidence pending — needs completed forward-return windows" note instead, so we don't show placeholder chrome as data.
- **New charts** (added once, no duplicate views of the same series):
  - Top-20 bucket distribution bar (Top Candidate / Candidate / High Potential but Risky / Watchlist) — teal → blue → amber → grey palette.
  - Shadow vs Official overlap bar (shadow-only / common / official-only) — blue / teal / violet.
  - Universe composition donut (Nifty50 / Next50 / Midcap150 / ETF) — blue / teal / violet / amber.
- **Color psychology tokens** (add to dashboard CSS + reuse in Qt QSS):
  - `--pos: teal (#2dd4bf)`, `--good: green (#4ade80)`, `--info: blue (#60a5fa)`, `--warn: amber (#f59e0b)`, `--neg: rose (#f43f5e)` used only for veto/critical, `--accent: crimson gradient` reserved for primary tabs / CTAs / verdict headers.
  - Ensure per-card glow uses the card's variant color (already added in v4.7 for Qt but not consistently applied in the HTML dashboard cards) via a `--glow` CSS variable set per `.card--pos/.card--info/.card--warn/.card--accent`.

## 3. Fix the "black rectangle inside cards" artifact

Root cause: v4.7 cards use `background: var(--card-bg)` on the outer glass surface, then an inner `<div class="card__body">` with an explicit dark solid fill and no border-radius, so it renders as a sharp black rectangle inside the rounded glass frame. Present in DQ / Validation / Trade Plan cards in both the Qt widgets (`QFrame` children) and the HTML dashboard cards.

- In Qt (`run_app.py`): give the inner body frame `background: transparent; border: none;` and let the outer card own the fill + radius. Remove the fixed dark `#0b0b12` on `QFrame#cardBody`.
- In the HTML dashboard (`dashboard_html_builder.py`): drop the inner `background` on `.card__body`, keep only `padding`. Round any inner surfaces to `border-radius: inherit` or 12px.
- Sweep the trade-plan mini stat blocks (BUY ZONE / STOP / HOLD / TARGET / SCORE) so their inner tile has the same rounded, transparent-over-glass treatment rather than a solid dark chip.

## 4. Compare tab rebuild (`run_app.py` — new `CompareView` widget)

Currently a single 1-row `QTableView` of raw compare CSV columns. Replace with a proper comparison surface driven by `output/top20_comparison.csv` (or the shadow-vs-official join already produced by the pipeline):

- **Summary strip** (4 KPI cards): Jaccard@20, Spearman full, Avg |ΔRank|, Verdict agreement (Official vs Shadow).
- **Side-by-side table** with columns: Symbol · Bucket (Official) · Bucket (Shadow) · Rank Official · Rank Shadow · ΔRank · Score Official · Score Shadow · ΔScore · Movement chip (↑ / ↓ / = with color).
- **Top movers panel**: two compact lists — biggest 5 rank improvements (shadow better) and biggest 5 rank regressions, each as a card row.
- **Scatter** (Official Score vs Shadow Score) using `QtCharts.QScatterSeries` with a y=x reference line, so overlap and drift are visible at a glance.
- Empty-state: when the CSV has only header + neutralized shadow row, render an info card ("Shadow run neutralized — insufficient shadow evidence") instead of an empty table.

## 5. In-app rendering of Report sections

The Validation tab and Trade Plan tab still show a big block of raw markdown at the bottom (`## Spread Summary`, `| Reason | Count |`, etc.). Add a small `md_to_widgets.py` helper (or inline into `run_app.py`) that:

- Parses the known section headers (`## Validation Verdict`, `## Spread Summary`, `## Bucket Performance`, `## Missing / Unmatured Signal Diagnostics`, `## Interpretation rules`, `# Trade Plan Report`, `## Report Notes`) into `QGroupBox`-style glass panels with proper header typography.
- Renders markdown tables as real `QTableView`s (headers styled, zebra rows) instead of monospaced pipe text.
- Renders bullet lists as `<ul>` inside a `QTextBrowser` with the app's CSS applied (font, color tokens, spacing) so text no longer looks like pasted plain-text.
- Renders bold spans (`**Insufficient Evidence**`) as proper `<strong>` with the semantic color of the enclosing verdict.
- Empty-state per section: if the source line reads `_No spread summary yet..._`, render an italic muted note card instead of the raw underscore-wrapped text.

Apply this renderer in both `ValidationView` and `TradePlanView`, replacing the current `QLabel`/`QTextEdit` that shows raw text.

## 6. Guard against the regressions this patch is fixing

Add a tiny startup self-check in `run_app.py` that logs (to the Activity drawer):

- version string in header vs `APP_VERSION` (must match, no descriptor suffix).
- presence of the new dashboard blocks (`#signal-maturity-kpis`, `#universe-composition`, `#shadow-overlap`, `#bucket-distribution`) after the WebEngine finishes loading — if any missing, log a warning so we notice on the next run instead of silently reverting.

## Technical section (for implementers)

Files changed:

- `run_app.py`
  - `APP_VERSION = "4.8"`; header pill + `setWindowTitle` updated.
  - `QFrame#cardBody { background: transparent; border: none; }`; remove `#0b0b12` fills; ensure `QFrame#card` owns radius + glow.
  - New `CompareView(QWidget)` replacing the current compare `QTableView` block.
  - Wire `md_to_widgets.render(md_text, parent_layout)` inside `ValidationView` and `TradePlanView` in place of the current raw text widget.
  - Startup self-check in `MainWindow.showEvent`.
- `dashboard_html_builder.py`
  - Remove `renderMaturityDonut()` + 5 evidence mini-cards; add `renderSignalMaturityKPIs()` + horizontal stacked bar.
  - Add `renderBucketDistribution()`, `renderShadowOverlap()`, `renderUniverseComposition()`.
  - Replace inline colors with `--pos / --good / --info / --warn / --neg / --accent` tokens; set per-card `--glow`.
  - Drop `.card__body { background: #... }` fills; inherit radius.
- New: `md_to_widgets.py` (markdown → Qt widget tree).
- Package as `nse_quant_engine_v4_8_patched.zip` (drop-in replacement — safe to overwrite v4.7).

Note: Compare-tab scatter needs `PyQt6-Charts` (already a v4.7 dep). If unavailable at runtime, fall back to an SVG scatter rendered by Matplotlib to a `QSvgWidget`.
