## Additive output retention cleanup

Add a single, fail-soft retention step that prunes accumulating dated artifacts to the last N, with an explicit protected list and an auditable log. No changes to how "latest" files or rolling history CSVs are written. Evidence bundle keeps its own zip prune (confirmed at `core/evidence_bundle.py:273-274`); the new step will NOT touch `insight_bundle_*.zip` to avoid double-owning.

### 1. Config — `core/config.py`
Add:
```python
# Retention for accumulating dated artifacts (dashboards, dated score snapshots,
# enricher backups, TER debug xlsx). Keeps the most recent N per pattern.
# Set to 0 to disable pruning entirely (safety escape hatch).
# Never applies to PROTECTED_FILES / rolling history CSVs.
RETENTION_KEEP_N = 10
```

### 2. New module — `core/cleanup_outputs.py`
Fail-soft `run_cleanup(base_dir)`; never raises. Structure:

- `PROTECTED_FILES` (set of exact filenames, checked before any delete):
  - `score_history.csv`, `signal_history.csv`, `forward_return_history.csv`,
    `alpha_score_history.csv`, `cross_sectional_spread_by_date.csv`,
    `shadow_vs_official_history.csv` (the shadow-vs-official ledger),
    plus `latest_scores.csv`, `dashboard_latest.html`, and every other
    `*_latest.*` / non-dated "current" file — belt-and-braces so a future
    pattern edit can't sweep them.
- `PRUNE_PATTERNS` (list of `(root, glob)`):
  - `output/`: `latest_scores_*.xlsx`, `dashboard_*.html` (dated only —
    guard excludes `dashboard_latest.html` via PROTECTED_FILES),
    `latest_scores_v4_shadow_*.xlsx` if any dated variants exist.
  - `manual_etf_quality_backup_*.csv` at project root (enricher backups).
  - `data/ter_tracking_debug/`: `*.xlsx` (the `_%Y%m%d_%H%M%S_*` debug dumps).
- Explicitly EXCLUDED (owned elsewhere): `output/insight_bundle_*.zip` —
  `core/evidence_bundle.build_bundle` already prunes to `keep_last_n=10`.
  Add a code comment noting this to prevent double-implementation.

Algorithm per pattern:
1. If `RETENTION_KEEP_N <= 0` → skip all pruning (return early, log "disabled").
2. Glob → filter out any path whose `name` is in `PROTECTED_FILES`.
3. Sort by mtime ascending; delete all but the last N.
4. Wrap each unlink in try/except; count successes.
5. Append one row per pattern to `output/cleanup_log.csv` with columns
   `timestamp, root, pattern, kept, removed, removed_files` (semicolon-joined
   basenames, truncated). Header written if file missing.

### 3. Orchestrator wiring — `orchestrator.py`
Append as the LAST step (after `dashboard_html_builder`, after any evidence bundle step if present):
```python
Step("cleanup_outputs", _module("core.cleanup_outputs", "run_cleanup"), skippable=True)
```
`skippable=True` so a cleanup failure can never fail a run.

### 4. Test — `tests/test_cleanup_outputs.py`
Using `tmp_path`:
- Create 15 fake `dashboard_2026-06-DD.html`, 15 `latest_scores_2026-06-DD.xlsx`, plus `dashboard_latest.html`, `score_history.csv`, `forward_return_history.csv`, `alpha_score_history.csv` in a mock `output/`.
- Set mtimes ascending so ordering is deterministic.
- Run `run_cleanup(tmp_path)` with `RETENTION_KEEP_N=10` (monkeypatch).
- Assert: 10 newest dated files per pattern remain; 5 oldest per pattern gone; every PROTECTED_FILES entry still exists; `cleanup_log.csv` written with expected columns.
- Second test: `RETENTION_KEEP_N=0` → nothing deleted.
- Third test: even if a protected file matches a prune glob (rename edge case), it is not removed.

### Non-goals / guardrails
- No change to `evidence_bundle.py` (confirmed its `keep_last_n` prune covers `insight_bundle_*.zip`).
- No change to `latest_*` self-overwriting writes.
- No change to any `*_history.csv` append logic.
- No LLM, deterministic Python only, `output/cleanup_log.csv` makes every deletion auditable.
