
# Plain-English layer for the dashboard

Purely additive pass on `desktop/nse_quant_engine/dashboard_html_builder.py`. No other files touched. No new computation, no LLM, no network. All new text is generated deterministically in Python from values already assembled in `_payload()` (verdict, `progress.effective_now/effective_target`, `progress.raw_now/raw_target`, `progress.matured/maturing/total`, top-5 rows, shadow state). If a value is missing the copy says "not yet available" in plain words — never a fabricated number, never softer than the underlying verdict.

The existing glassmorphic theme, glow system, every honesty caption, verdict chip / progress bar / streak strip / watchlist ribbons / alpha evidence table / ETF gaps / scatter `<details>` all stay exactly as they are. This layer sits *around* them.

## 1. Top "Plain English summary" card

New `.g-violet` glass card injected as the **first** element inside `<main>`, above the existing header sub-line and above the Progress-to-a-verdict row. Renders 3–4 short sentences composed from a small template keyed on `payload.progress.state` (`green` / `amber` / `red` / `neutral`).

Template family (values interpolated from payload — never hand-typed numbers):

- **neutral / amber (Insufficient History, Insufficient Independent History, Insufficient Breadth, Insufficient Statistical Evidence, No Proven Edge Yet):**
  "Today's result: no action. The tool is still learning whether its stock rankings actually work, and it doesn't have enough history yet ({effective_now} of ~{effective_target} independent days needed). Everything below is practice data for watching only — not advice to buy anything. Keep running it daily; it'll tell you when it has real evidence."

- **red (Validation Negative):**
  "Today's result: do not act on the picks below. Over the days measured so far, the tool's rankings have not beaten costs — the edge is negative. Treat the lists below as a record of what the model *would* have picked, not as ideas to buy."

- **green (Validation Positive):**
  "Today's result: the tool's rankings have finally cleared their evidence bar ({effective_now} of {effective_target} independent days, plus the statistical checks). This still means 'worth a closer look,' not 'will make money.' Read the 'Before you ever act on this' panel at the bottom before doing anything with real money."

- **unavailable (both `validation_status.json` and the markdown report missing):**
  "Today's result: verdict not yet available. The validation step hasn't run in this output folder, so the tool has no opinion to share yet. Run the workflow end-to-end, then reopen this page."

When `progress.effective_now` or `progress.effective_target` is missing, the parenthetical becomes "(day count not yet available)" instead of a number. The card also includes one small caption underneath: "Plain-English summary — auto-generated from today's validation output. See the technical panels below for the numbers behind it."

## 2. Glossary tooltips on finance/stats terms

Static Python dictionary `PLAIN_GLOSSARY` near the top of the file. One-sentence, plain-English definitions for the terms actually shown on the dashboard:

- NAV, TER, tracking error, AUM
- IC, residual IC, t-stat, bootstrap probability, spread, hit rate
- momentum, quintile, drawdown, RSI, volatility, IV rank, delivery %
- effective validation dates, raw validation dates, matured / maturing signals
- shadow vs official, overlap, veto, regime

Example entries (final copy in the code, not here): "IC — how well the tool's ranking of stocks lined up with what actually happened next. 0 means no relationship; higher is better."; "t-stat — a rough measure of how unlikely the result is to be luck. Bigger numbers mean more convincing, not necessarily more profitable."; "effective validation dates — how many *independent* days of evidence the tool has, after removing days that overlap with each other."

Rendering: a tiny helper `_gloss(term, label=None) -> str` returns
`<span class="gloss" tabindex="0" aria-describedby="tt-{slug}">{label or term}<span class="tt" role="tooltip">{definition}</span></span>`.
CSS (added to the existing `<style>` block, matching the current glass tokens): `.gloss` gets a dotted underline in the current muted color; `.tt` is absolutely positioned, hidden by default, revealed on `:hover`, `:focus-within`, and `.gloss:active .tt` (tap on touch). No JS — pure CSS hover/focus, keyboard-reachable via `tabindex="0"`, screen-reader-labeled via `aria-describedby`.

Wrapping pass: the existing headings and captions that mention any dictionary term (e.g. "IC ≥ min_ic", "t-stat", "effective validation dates", "tracking error", "NAV", "TER", "RSI × volatility map", "momentum", "quintile", "drawdown") are rewritten to use `_gloss(...)` for the first occurrence in each panel only, so tooltips are discoverable without visually peppering every line. Panel structure, order, and numeric values are unchanged.

## 3. Per-candidate "in plain words" line on Top-5 cards

Inside the existing Top-5 card template, one extra `<p class="plain">` under the current body, above the existing "Also in shadow Top 5" chip when present. The sentence is composed by a small deterministic helper `_plain_card_line(row, verdict_state) -> str` from fields already on each card row — no new computation:

Trend fragment (from momentum / trend rank if present, else RSI): "strong recent price trend" / "mixed recent price trend" / "weak recent price trend" / "recent price trend not yet available".
Calm fragment (from volatility bucket if present): "calm price swings" / "moderate price swings" / "choppy price swings" / omitted if missing.
News fragment (from sentiment/veto flags already on the row): "no bad news found" / "some negative news flagged" / "governance concern flagged — see veto note" / omitted if the field isn't present.
Suffix (always, verdict-aware):
- non-positive verdict: " — but this is a watch-only note, not a recommendation, because the tool hasn't proven its picks work yet."
- Validation Positive: " — the tool's rankings have cleared their evidence bar, but this is still 'worth a closer look,' not a buy signal."

Missing fragments are dropped rather than filled with placeholders; if every fragment is missing, the line becomes: "Not enough plain-language signals to summarise this candidate yet — see the numeric fields above." The trailing watch-only / worth-a-look suffix is always appended.

## 4. Permanent "Before you ever act on this" panel

New `.g-amber` glass panel injected near the bottom of `<main>`, immediately above the existing footer / Excel-ready summary line. Renders **regardless of verdict** (positive or not — the copy adapts, the panel does not disappear). Five bullets, calm tone, no fine-print styling:

- This is a personal research tool, not financial advice.
- It has never been proven to make money; it may never be.
- Even a "positive" verdict means "worth a closer look," not "will profit."
- Consult a SEBI-registered adviser before investing real money.
- Never invest money you can't afford to lose.

Heading: "Before you ever act on this". Sub-caption: "Always visible. Read it every time — including the days the tool looks confident."

## Implementation details (technical section)

Single file: `desktop/nse_quant_engine/dashboard_html_builder.py`.

- New module-level constants:
  - `PLAIN_GLOSSARY: dict[str, str]` — term → one-sentence definition.
  - `PLAIN_SUMMARY_TEMPLATES: dict[str, str]` — state → template string, with `{effective_now}` / `{effective_target}` placeholders and a "(day count not yet available)" fallback.
  - `PLAIN_DISCLAIMER_BULLETS: tuple[str, ...]` — the five bullets above.
- New helpers (module-level, pure functions of payload dicts, no I/O):
  - `_plain_summary_html(progress: dict) -> str` — picks template by `progress.state`, formats safely (missing numbers → fallback phrase), returns full glass card HTML.
  - `_gloss(term: str, label: str | None = None) -> str` — returns the inline span. Unknown term → returns the label/term unchanged (fail-soft, never raises).
  - `_plain_card_line(row: dict, verdict_state: str) -> str` — composes the sentence from present fields only.
  - `_plain_disclaimer_html() -> str` — renders the amber panel.
- CSS additions in the existing `<style>` block: `.plain-summary`, `.gloss` (dotted underline, cursor help), `.tt` (absolute, hidden, revealed on `:hover`/`:focus-within`/`:active`, uses existing glass tokens for background/border), `.plain` (card sub-line styling), `.plain-disclaimer` (amber panel). No changes to existing selectors; only additions.
- Template insertions in the HTML string:
  1. Plain-English summary card → first child of `<main>`.
  2. `_gloss(...)` wrap on first occurrence of each dictionary term per panel.
  3. `<p class="plain">{_plain_card_line(row, state)}</p>` inside the Top-5 card template.
  4. Permanent disclaimer panel → immediately above the footer / Excel-ready line.
- No changes to `_payload()` shape, to file reads, or to any existing panel's structure, ordering, or captions beyond wrapping terms in `_gloss(...)`.

## Verification

Render `dashboard_latest.html` from the current output folder (Insufficient History) and confirm:
- Plain-English summary card is the first thing on the page and reads the "still learning" template with real `effective_now / effective_target` numbers pulled from the payload.
- Every wrapped term shows a tooltip on hover, on keyboard focus (Tab), and on tap; the tooltip text matches the dictionary; the underlying number/heading is unchanged.
- Each Top-5 card has one extra `<p class="plain">` line ending in the watch-only suffix.
- The "Before you ever act on this" amber panel renders above the footer with all five bullets.
- Existing sections (Progress to a verdict, Market context, Shadow vs Official streak strip, Alpha Zoo evidence table, ETF gaps, watchlist ribbons, scatter `<details>`) are visually and structurally unchanged.

Then delete `validation_status.json` and `cross_sectional_validation_report.md` and reconfirm: the summary card falls back to "verdict not yet available" copy, no fabricated day counts appear, the disclaimer panel is still there, and no panel crashes.
