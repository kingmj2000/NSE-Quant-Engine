# v4.8 patch — repo-hosted workflow

Locked path: I import the v4.7 tree into this Lovable project as the source of truth, apply the v4.8 edits on top, and from now on you just pull the repo (or download the zip from Lovable / GitHub) instead of me shipping a patch zip each turn.

## Step 0 — Import v4.7 into the repo (one-time)

- Extract the uploaded `nse_quant_engine_v4_7_patched.zip` into `desktop/nse_quant_engine/` at the project root.
- Exclude: `__pycache__/`, `output/`, `data/` caches, `manual_etf_quality_backup_*.csv` (keep the latest one only), any `.git` metadata.
- Add `desktop/` to `.gitignore`-style excludes for Vite (`vite.config.ts` `server.fs` / build already ignores non-`src`, but confirm) so the web preview is unaffected.
- Add `desktop/README.md` explaining: this folder is the PyQt app, run `python run_app.py` from inside it, Lovable web preview is unrelated.

After Step 0 the Lovable file tree is the ground truth. Every future patch is a normal diff you can see in Lovable's code view; you download via **Code Editor → Download codebase** or connect GitHub for auto-sync.

## Step 1 — v4.8 edits (applied on top of the imported tree)

All paths below are under `desktop/nse_quant_engine/`.

1. **Auto-close fix + version**
   - `run_app.py`: `APP_VERSION = "4.8"`; header pill = `v4.8` (drop `· glassmorphic`); `setWindowTitle` + About match.
   - Wrap `MainWindow.__init__` and WebEngine load in try/except that logs to the Activity drawer (prevents silent QApplication teardown).
   - Hold `QWebEngineView`, `QTimer`, and chart-series refs on `self` so Qt GC doesn't kill the window.

2. **Per-element glow**
   - Add `--glow` CSS var per variant (`pos`=teal, `info`=blue, `warn`=amber, `good`=green, `accent`=crimson, `neg`=rose — rose only for veto/critical).
   - Mirror in Qt QSS via `QFrame#card[variant="…"]` selectors. No more blanket red glow.

3. **Dashboard visuals (`dashboard_html_builder.py`)**
   - Remove maturity donut + 5 empty evidence mini-cards.
   - Add: Signal-Maturity KPI pair (Matured / Awaiting) + horizontal stacked bar by 5D/21D/63D horizon.
   - Add: Top-20 Bucket Distribution bar, Shadow-vs-Official Overlap bar, Universe Composition donut.
   - Retire crimson from categorical palettes (reserved for primary tabs / CTAs / verdict headers only).

4. **Kill "black rectangle inside cards"**
   - Qt: `QFrame#cardBody { background: transparent; border: none; }`, drop `#0b0b12` fills, outer `QFrame#card` owns radius + glow.
   - HTML: remove `.card__body { background: … }`, inherit radius, round inner tiles (BUY ZONE / STOP / HOLD / TARGET / SCORE).

5. **Compare tab rebuild (`CompareView` in `run_app.py`)**
   - 4 KPI cards: Jaccard@20, Spearman, Avg |ΔRank|, Verdict agreement.
   - Side-by-side table: Symbol · Bucket O/S · Rank O/S · ΔRank · Score O/S · ΔScore · movement chip.
   - Top movers panel (5 up / 5 down).
   - Scatter (Official vs Shadow score, y=x reference) via `QtCharts.QScatterSeries`; Matplotlib → `QSvgWidget` fallback.
   - Empty-state card when the shadow row is neutralized.

6. **Report rendering (new `md_to_widgets.py`)**
   - Parse section headers (`## Validation Verdict`, `## Spread Summary`, `## Bucket Performance`, `## Missing / Unmatured Signal Diagnostics`, `## Interpretation rules`, `# Trade Plan Report`, `## Report Notes`) into glass `QGroupBox` panels.
   - Markdown tables → styled `QTableView` (zebra rows, themed headers).
   - Bullet lists → themed `QTextBrowser` with app CSS.
   - Bold spans → `<strong>` in the enclosing verdict's semantic color.
   - `_No spread summary yet..._` and similar empties → italic muted note card.
   - Wire into `ValidationView` and `TradePlanView`, replacing raw `QLabel`/`QTextEdit`.

7. **Regression guard (startup self-check)**
   - Log to Activity drawer: header version == `APP_VERSION`, and presence of `#signal-maturity-kpis`, `#universe-composition`, `#shadow-overlap`, `#bucket-distribution` after WebEngine `loadFinished`. Warn (don't crash) if missing.

## Deliverable

- `desktop/nse_quant_engine/` committed to this Lovable project with all v4.8 changes applied.
- No standalone patch zip going forward; download the repo when you want to run it.

## Technical notes

- Files edited: `desktop/nse_quant_engine/run_app.py`, `desktop/nse_quant_engine/dashboard_html_builder.py`.
- File added: `desktop/nse_quant_engine/md_to_widgets.py`.
- Deps unchanged (`PyQt6-Charts` already present; Matplotlib fallback path added).
- No pipeline / scoring / CSV-schema changes — pure UI + rendering patch.
- Web app under `src/` is untouched.

Approve to proceed and I'll execute Step 0 + Step 1 in one build pass.
