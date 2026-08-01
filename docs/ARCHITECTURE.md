# Architecture

## Design principle

> **Python computes. Validation measures. AI explains. The human decides.**

- **Python computes** — every score, rank, factor, cost estimate and portfolio
  weight is produced by deterministic Python in `desktop/nse_quant_engine/`.
  No model output, no LLM, no external service participates in producing a
  number.
- **Validation measures** — a separate cross-sectional layer asks a single
  question: *does this ranking beat the benchmark after costs, out of sample?*
  Its most valuable possible answer is often "No Proven Edge Yet."
- **AI explains** — optional prompt templates and an evidence bundle exist so a
  human can ask an LLM to *narrate* what the numbers already say. Explanation
  never flows back into scores.
- **The human decides** — the app makes no orders and has no broker
  integration. It is a screener with an audit trail.

## Repository layout

```
desktop/nse_quant_engine/
  core/                 engine modules (scoring, validation, portfolio, data)
  news/                 context-only news pipeline (sources, cache, dedup)
  ui/                   PySide6 desktop panels
  tests/                pytest suite (offline; fixtures + mocks)
  data/                 LOCAL runtime caches      (git-ignored)
  output/               LOCAL results & evidence  (git-ignored)
  orchestrator.py       headless full pipeline
  run_app.py            desktop application entry point
docs/                   this documentation
examples/sample_output/ synthetic artifact shapes
src/                    minimal TanStack Start web shell (project landing page)
```

## Pipeline

The headless run (`python orchestrator.py --all`) proceeds broadly as:

1. **Universe build** — NSE equities and ETFs (`universe_builder.py`).
2. **Price fetch / incremental cache** — raw prices cached to avoid
   re-download and adjustment drift (`core/price_cache.py`).
3. **Optional context fetch** — FII/DII flow, bulk deals, delivery %, IV rank,
   fundamentals. Every one of these is *optional*; failure degrades gracefully
   and is recorded in `data/data_health.json`.
4. **Factor computation** — momentum (primary), trend and relative strength as
   soft confirmation gates, fundamental/quality factor, ETF microstructure.
5. **Sector context and neutralisation** — sectors with fewer than 5 members
   are skipped rather than neutralised on noise.
6. **Scoring** — produces `Raw_Score` and `Confidence_Adjusted_Score` (CAS).
7. **Ranking** — see below.
8. **Alpha zoo evaluation** — walk-forward IC with an incremental/residual gate
   so a new alpha must add information beyond existing survivors.
9. **Validation** — writes `output/validation_status.json` (authoritative).
10. **Expected value / costs** — cost-aware EV per holding day, blank until
    validation is positive.
11. **Portfolio selection and trade plan** — ship-gated; watchlist-only unless
    validation says otherwise.
12. **Daily changes** — structured diff in `output/daily_changes.json`.
13. **News digest** — context only, never fed back.
14. **Dashboard + evidence bundle** — HTML dashboard and portable bundle.
15. **Retention cleanup** — prunes accumulating artifacts, never touching
    `PROTECTED_FILES` (`core/cleanup_outputs.py`).

## Ranking authority

There is exactly one official order:

```
Confidence_Adjusted_Score DESC, Symbol ASC
```

- `Rank` and `Opportunity_Rank` are official and derive from that order.
- `Raw_Score_Rank` is **diagnostic only** and must never drive selection.
- `core/candidate_selection.py` is the single source of canonical ordering; the
  scoring engine, validation, trade plan, portfolio selection and every UI
  panel read from it.
- The shadow engine never supplements or reorders official lists. The
  Candidates Workbench offers strict **Official / Shadow / Compare** modes;
  Compare keeps official ordering and appends shadow columns.

## Watchlist-only behaviour

Until `validation_status.json` reports a positive verdict, every candidate is
labelled **Watchlist Only** and the UI shows a watchlist ribbon and banner.
This is enforced by the ship gate, not by UI text alone.

## Adaptive weighting

An adaptive weight layer exists but is **dormant** (`ADAPTIVE_ENABLED = False`).
It requires a minimum number of *effective* validation dates, is bounded by a
maximum total drift guardrail, and must be enabled manually by an operator who
understands the trade-off. The default branch keeps it off.

## News

The news pipeline collects public headlines and exchange filings for candidate
symbols, deduplicates them per `(Symbol, Is_Official_Filing)`, classifies event
categories with deterministic regex rules, and records source health. It has
**zero** score impact by construction — no news field is an input to any factor,
score, rank, weight or trade-plan decision.

## Testing

`tests/` runs fully offline. Network-dependent behaviour is mocked or served
from `tests/fixtures/`. Reader/schema tests import without PySide6 so CI can run
headless; UI tests set `QT_QPA_PLATFORM=offscreen`.
