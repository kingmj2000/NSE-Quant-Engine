# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed — manual refresh now actually refreshes

- **"Refresh optional feeds now" honoured the cache and skipped the fetch.** The
  button called `refresh_all()` with default arguments, so any feed whose CSV was
  still inside its freshness window was skipped and the log read
  `fresh (<24h) — skipping fetch` — the opposite of what pressing a manual refresh
  button asks for. `refresh_all()` and all six fetchers now take `force`, and both
  manual entry points (`ui/decision_center.py`, `run_app.py`) pass `force=True`.
  Freshness windows exist to prevent redundant AUTOMATIC fetches, not to override
  a deliberate one.
- **The scheduled pipeline step stays cache-aware on purpose.**
  `orchestrator._run_optional_feeds()` deliberately does not force, so a full run
  still skips feeds it already has rather than hammering public endpoints. A test
  pins this asymmetry via AST so a future edit cannot collapse the two paths.
- **`refresh_all()` did not honour its documented "never raises" contract.** Only
  `delivery_pct` and `iv_rank` were wrapped; a network error inside `fii_dii`,
  `bulk_deals`, `fundamentals` or `earnings` propagated out and could abort the
  pipeline step whose docstring promises it is always non-fatal. All six feeds are
  now wrapped, and on failure the status falls back to whether a usable cache
  exists on disk — a failed refresh is not the same as missing data.

### Changed
- `verify_repo_health.py` asks git whether `output/` and `data/` are TRACKED
  rather than whether they exist on disk. A live run folder is supposed to contain
  them, so the old check failed in exactly the place it was most likely to be run.
  Outside a checkout it now reports not-applicable.

### Added
- `tests/test_manual_force_refresh.py` — 16 tests: every fetcher accepts `force`,
  a fresh cache is skipped without it (asserted by making a network session raise
  if opened), `force=True` gets past the short-circuit for all six feeds, a forced
  refresh that fails offline never deletes cached data, and the manual/pipeline
  asymmetry holds.

### Fixed — Overview counters silently zeroed by an out-of-scope name

- `ui/decision_center.py::_section_validation_progress()` called
  `read_maturation_progress(OUT, ...)`, but `OUT` is a local of `refresh()` — a
  different method. The resulting `NameError` was swallowed by a bare
  `except Exception: pass`, so every validation-progress counter read 0 while the
  HTML dashboard, reading the same files directly, showed 11,647 matured. Now
  uses `self.OUT`, and the handler prints the failure instead of hiding it: a
  zeroed counter must be distinguishable from one that could not be computed.

### Added
- `tools_scan_undefined_names.py` — scope-aware AST scan for names loaded but
  never bound in any enclosing scope. `compileall` and imports both pass on this
  class of defect because `NameError` only fires at call time. Nested functions,
  lambdas and comprehensions are analysed in their own scopes, which keeps the
  output free of false positives. Wired into `verify_repo_health.py`.
- `verify_repo_health.py` — 28 checks covering every fix from the audit sessions
  plus the pattern scans. No network, no pytest, no PySide6; suitable for CI.

### Fixed — history dedup, dead cross-module keys, cleanup

- **`append_history` never deduplicated, so every same-day re-run duplicated the
  whole row set.** The dedup compared raw values: a history file round-trips
  through `read_csv` so its dates return as the string `"2026-08-12"`, while
  freshly built rows hold `datetime.date`/`Timestamp`. Those never matched, so
  `score_history.csv`, `signal_history.csv` and `alpha_score_history.csv` gained a
  full duplicate set (one row per instrument) on each re-run. This is not just
  bloat: `cross_sectional_validation.make_detail()` merges forward returns against
  score history on (Signal_Date, Symbol), so duplicate keys fan the merge out and
  each matured observation is counted once per re-run — pseudo-replication that
  inflates Obs, shrinks standard errors and overstates t-statistics. The key is
  now canonicalised on both sides; existing duplicate-laden files self-heal on the
  next write, keeping the most recent row per key. Timestamp columns are
  deliberately NOT collapsed to a day so `run_log.csv` keeps one row per run.
- **`regime_change` could never fire.** `core/daily_changes.py` reads
  `previous_regime` from `macro_context.json`; nothing wrote it. The writer in
  `trade_plan_builder.py` now carries the prior run's regime forward, so a regime
  change actually appears in Today's Changes.
- **Dead key read in the Portfolio tab.** `run_app.py` looked for a capitalised
  batch-verdict key before falling back; `portfolio_validation.json` only ever
  writes `verdict`. Dead branch removed.

### Changed
- `append_history` moved to `core/history_io.py`. It previously lived in
  `nse_quant_engine.py`, which imports yfinance at module scope and therefore
  cannot be imported in CI or without network dependencies — the helper was
  untestable where it mattered most.

### Removed
- `phase_1a_audit_gaps.py`, `phase_1b_fill_etf_gaps.py` — one-off ETF-gap
  migration scripts, unreferenced by any module, launcher, workflow or doc.
  `phase_1a` also printed instructions to run `phase_1b_implement_fallbacks.py`,
  which does not exist.
- `missing_ter_list.txt` — empty runtime artifact, already covered by
  `.gitignore` but still tracked.

### Added
- `tests/test_history_dedup.py` — 11 regressions covering same-day re-runs across
  date/Timestamp/string forms, self-healing of pre-existing duplicates,
  preservation of distinct days, canonical on-disk date form, timestamp keys
  keeping full precision, and the two dead-key regressions.

### Fixed — evidence accumulation, maturation counters, shadow KPIs

- **forward_return_history.csv was rebuilt, not accumulated** — survivorship
  bias. `validation_builder` recomputed every forward return each run from
  `signal_history` against the CURRENT `raw_prices_latest.csv`, then overwrote the
  file. Once a symbol left the universe its previously matured returns could no
  longer be recomputed and silently disappeared (matured count fell ~10k -> ~7k,
  surfacing as "Symbol not found in current raw price file"). Retaining only
  index survivors biases measured edge upward. Added
  `merge_forward_history()`: this run's rows win on conflict, rows it cannot
  recompute are retained, dedup key `(Signal_Date, Symbol, Horizon_Days)` is
  normalised so a CSV round-trip cannot create phantom duplicates, and an
  unreadable prior file never silently deletes evidence.
- **"Awaiting maturation" was structurally always 0** and the maturation rate
  always 100%. Pending signals are written to
  `forward_return_missing_signals.csv`; `forward_return_history.csv` holds only
  matured rows, so counting `Net_Forward_Return.isna()` on it could only ever
  return zero. New `core.ui_readers.read_maturation_progress()` reads pending and
  unmatchable counts from the correct file; both the Overview strip
  (`ui/decision_center.py`) and the KPI grid (`run_app.py`) now use it, and
  unmatchable signals are reported separately instead of being invisible.
- **Official vs Shadow KPIs read keys nobody wrote.** `run_app.py` looked for
  `jaccard_at_20` / `avg_abs_delta_rank`; the writer emits `jaccard_top25` and
  did not emit a rank delta at all, so both cards rendered a permanent "—". The
  report now emits `overlap_top_n` and `avg_abs_delta_rank`, the UI reads the
  real key names, and the card is labelled with the true overlap depth (25, not
  20).
- **Misleading neutralized-panel copy.** "Shadow run neutralized — insufficient
  shadow evidence ... not enough matured signals" described forward-return
  maturation, but the panel fires when `shadow_vs_official.csv` has under two
  comparable rows. Reworded to say what is actually missing.

### Added
- `diagnose_validation_dates.py` — read-only diagnostic explaining a zero
  raw/effective date count against real local data: schema-v1 vs v2 date spans,
  whether v2 dates have matured, per-date instrument counts against the
  ten-instrument floor, missing-signal reasons, and price-file coverage.
- `cross_sectional_validation` now reports the schema-v2 gate instead of leaving
  a silent zero: joined rows, schema-v2 rows, dropped rows and distinct v2 dates
  are printed and written to `validation_status.json` as
  `schema_filter_diagnostics`.
- `tests/test_evidence_accumulation.py` — 10 regressions covering survivorship
  retention, conflict precedence, idempotency, date round-trips, corrupt-file
  handling, pending/unmatchable counting, and writer/reader KPI key agreement.

### Fixed — Candidates tab refresh crash

- `CandidatesWorkbench._reload_combo()` was called at four sites in `refresh()`
  and `_on_mode_changed()` but never defined, so every pipeline run and every
  "reload last run" ended with `Candidates tab refresh failed:
  'CandidatesWorkbench' object has no attribute '_reload_combo'` — after all 20
  steps reported `ok`. The method now exists: it repopulates a filter combo with
  `[placeholder] + values`, keeps the placeholder at index 0 (`_apply_filters`
  treats `currentIndex() > 0` as "a real filter is selected"), preserves the
  user's current selection when it survives the refresh, and blocks signals
  during the rebuild so `clear()` cannot re-enter `_apply_filters` against a
  half-populated widget.

### Fixed — integrity fail-closed pass

Four defects in which a check that *could not be performed* was recorded as a
check that *passed*. All four sat on the path that only opens once validation
turns positive, and all four survived a fully green test suite because the
existing tests only exercised the branches where the evidence was present.

- **Portfolio validation failed open on missing artifacts**
  (`core/portfolio_validation.py`). An absent or symbol-less `top5_*` file was
  skipped rather than flagged, so with a positive verdict and a valid Top-5 the
  batch could return `Ship` with no artifacts on disk. The report now carries
  `missing_artifacts`, `artifact_completeness` and `symbol_order_aligned`; a
  missing artifact, a missing official Top-5, a symbol-set mismatch or a
  symbol-**order** mismatch each force `Downgrade_To_Watch`. Symbol readers now
  preserve on-disk row order (they previously sorted, which made order
  unverifiable).
- **Shadow comparison could compare the official score against a copy of
  itself** (`nse_quant_engine_v4_shadow.py`, `shadow_vs_official_report.py`).
  The shadow frame inherits official columns via `out = old.copy()`, and the
  report's fallback chain preferred `Confidence_Adjusted_Score` — reporting
  Spearman 1.00 / Jaccard 1.00 as perfect agreement. Added an explicit
  `V4_Confidence_Adjusted_Score` (with `V4_CAS_Basis`), ranked `V4_Rank` from it
  under the official CAS-desc/Symbol-asc rule, re-sourced the official baseline
  from CAS instead of `Final_Score`, and added `sanitize_shadow_columns()` so no
  bare official score column survives into a shadow artifact. All fallbacks
  removed.
- **Same root cause in two more consumers.** `ui/candidates_workbench.py` and
  `dashboard_html_builder.py` also resolved the shadow score by fallback chain,
  so Shadow mode and the dashboard "shadow Top-5" were displaying and sorting by
  *official* scores. Both now require `V4_Confidence_Adjusted_Score` by name.
- **Expected value ignored filters it could not apply**
  (`core/expected_value.py`). `_apply_filters` silently dropped unknown columns,
  and the shadow report filtered on a column and bucket value this engine never
  writes (the real column is `Signal_Bucket`), so the filter was ignored on every
  run and an unfiltered EV was published under a filtered label. EV now returns
  `missing_filter_columns` and refuses to compute when a filter column is absent.
  Added a schema guard so a legacy `Date,Symbol,Fwd_Return` history returns a
  status dict instead of raising `KeyError('Horizon_Days')`.

### Changed
- Shadow governance is explicit everywhere: `CURRENT-RANKING DIAGNOSTIC ONLY`.
  Promotion, champion and switch-recommendation branches removed from
  `shadow_vs_official_report.py`; the shadow ledger state can no longer reach
  `green`; shadow EV reports `INSUFFICIENT_SHADOW_HISTORY`.
- `docs/VALIDATION_METHODOLOGY.md` documents the fail-closed ship gate;
  `docs/ARCHITECTURE.md` documents shadow governance and exact score identity.

### Added
- `tests/test_ui_method_contracts.py` — AST guard asserting no class in the
  engine calls a private method it never defines (the `_reload_combo` class of
  defect raises `AttributeError` at call time, so imports and `compileall` both
  pass), plus a Qt-free behavioural test of `_reload_combo` itself. Runs
  headless, no PySide6 required.
- `tests/test_integrity_fail_closed.py` — 20 regression tests covering the
  absent-evidence branches: missing artifacts (all, and each one individually),
  symbol-less artifacts, missing official Top-5, symbol-order mismatch, the
  still-working Ship path, `portfolio_validation.json` field coverage, shadow
  column sanitisation, self-comparison refusal, absence of promotion language,
  and EV filter/schema fail-closed behaviour.
- Completed the Ship-path fixture in `tests/test_new_modules.py`, which wrote
  only three of the five critical artifacts and so asserted the failing-open
  behaviour.

### Notes
- The **official scoring formula is unchanged**. No new signals, indicators or
  features. No validation history reset. Ranking schema stays V2.

## [Unreleased] — public-repository scaffolding

### Added
- Public-repository scaffolding: GitHub Actions CI (Python 3.11 / 3.12 + web
  lint & build), Dependabot, `SECURITY.md`, `CONTRIBUTING.md`, issue and
  pull-request templates.
- `docs/ARCHITECTURE.md`, `docs/VALIDATION_METHODOLOGY.md`,
  `docs/DATA_SOURCES.md`, `THIRD_PARTY_NOTICES.md`.
- `examples/sample_output/` — synthetic score, validation, news-digest and
  daily-change artifacts documenting output shapes.
- `desktop/nse_quant_engine/.env.example`.
- Real landing page for the Lovable/TanStack web shell (replaces placeholder).

### Changed
- Ignore rules for Python caches, virtualenvs, coverage, credentials, OS/editor
  files and all engine runtime data/outputs (`desktop/.gitignore`).
- README rewritten to match the actual repository.

### Notes
- No scoring, ranking, validation, news, adaptive-weighting, portfolio or
  desktop-UI behaviour was changed.

## Earlier work

Prior history predates this changelog. Highlights, in rough order:

- Clean core v4: momentum de-triple-counting, absolute filters, incremental
  price cache, structured `validation_status.json`, single `config.py`.
- Fundamental/quality factor and cost-aware expected value.
- Professional-desk layer: macro regime, sector/peer context, event calendar,
  FII/DII + bulk-deal institutional flow, walk-forward backtest, EV/Kelly,
  portfolio ship-gate, regime tilt, rebalance diff, evidence bundle.
- Sector neutralisation, turnover-aware alpha weighting, dormant adaptive
  weights, residual-IC gate, Bayesian shrinkage on hit-rate/IC.
- Authoritative ranking migration to `Confidence_Adjusted_Score` (schema v2).
- Decision Center, Candidates Workbench and News & Events desktop UI.
- Context-only news pipeline with symbol-scoped dedup and source health.
- Output retention/cleanup step with protected files.
