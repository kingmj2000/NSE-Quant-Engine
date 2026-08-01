# Changelog

All notable changes to this project are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
