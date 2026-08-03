# NSE Quant Engine

A local, offline-first **screener and analytics engine** for NSE (Indian
equities & ETFs), with a PySide6 desktop UI. A minimal TanStack Start web page
lives at the repository root purely as a project landing page — the engine is
**not** a web app.

> **Honest goal and limitation.** This is a screener that, once validated, can
> tell you whether its ranking beats a benchmark **after costs**. It is *not* a
> low-risk / high-profit / short-hold oracle. The validation layer's most
> valuable possible answer is often **"No Proven Edge Yet."** Believe it when it
> says so. Until then the engine stays **watchlist-only** by design.

**Python computes. Validation measures. AI explains. The human decides.**

## Screenshots

Repository-safe screenshots are not committed yet, because current runs contain
real portfolio context. Placeholders:

| View | Placeholder |
|---|---|
| Decision Center | `docs/images/decision-center.png` _(to add)_ |
| Candidates Workbench | `docs/images/candidates-workbench.png` _(to add)_ |
| HTML dashboard | `docs/images/dashboard.png` _(to add)_ |

If you contribute screenshots, redact symbols and amounts first.

## Repository layout

```
desktop/nse_quant_engine/   the actual app — Python engine + PySide6 UI
  core/  news/  ui/  tests/
  data/    LOCAL runtime caches      (git-ignored, created on first run)
  output/  LOCAL results & evidence  (git-ignored, created on first run)
docs/                       architecture, validation, data sources
examples/sample_output/     synthetic artifact shapes (fabricated data)
src/                        TanStack Start landing page (optional)
```

## Install (desktop app)

Requires **Python 3.11 or 3.12**.

```bash
git clone <this-repo>
cd <this-repo>/desktop/nse_quant_engine
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run_app.py
```

Windows: double-click `run_app.bat`. macOS: `run_app.command`.
Optional extras for ETF metadata: `pip install -r requirements_optional_etf_metadata.txt`.
Optional settings: copy `.env.example` to `.env`.

See [`QUICKSTART_WINDOWS.md`](desktop/nse_quant_engine/QUICKSTART_WINDOWS.md)
for a first-run walkthrough.

## Daily run

Desktop UI — click **Run**. First run takes roughly 3–6 minutes (network);
tick **Skip fetch** afterwards to re-score from cache in under a minute.

Headless:

```bash
cd desktop/nse_quant_engine
python orchestrator.py --all              # full pipeline incl. fetch
python orchestrator.py --all --skip-fetch # re-score from cache
python nse_quant_engine_v4_shadow.py      # dormant adaptive/shadow layer
```

Windows batch equivalents: `run_full_workflow.bat`, `run_shadow_mode.bat`.

## Understanding the validation status

`output/validation_status.json` is the **authoritative** verdict — never read a
verdict out of a Markdown report.

| Verdict | What it means | What the app does |
|---|---|---|
| `No Proven Edge Yet` | Not enough effective validation dates, or shrunk IC/hit-rate indistinguishable from noise | Watchlist-only; expected value stays blank |
| Positive verdict | Ranking beat the benchmark after costs, out of sample | Trade plan and portfolio views unlock; still not advice |

History carries a schema version: v1 ranked by `Final_Score`, v2 (current)
ranks by `Confidence_Adjusted_Score`. Only v2 rows carry verdict authority.

Official ranking is **`Confidence_Adjusted_Score` descending, `Symbol`
ascending**. `Raw_Score_Rank` is diagnostic only.

Full detail: [`docs/VALIDATION_METHODOLOGY.md`](docs/VALIDATION_METHODOLOGY.md).

## Outputs (all under `output/`, all local)

| File | What it is |
|---|---|
| `latest_scores.xlsx` | Official engine scores & ranks |
| `latest_scores.csv` | Latest per-symbol score row (UI + shadow input) |
| `latest_scores_v4_shadow.xlsx` | Shadow engine scores (never reorders official) |
| `validation_status.json` | Canonical verdict (authoritative) |
| `cross_sectional_validation_report.md` | Human-readable validation narrative |
| `trade_plan_report.md` / `.xlsx` | Trade plan (ship-gated) |
| `daily_changes.json` | Entrants/exits, rank gainers, new risk flags |
| `news_digest.json` / `top_candidate_news.csv` | Context-only news & filings |
| `dq_report.md` | Data-quality health score and field fill rates |
| `dashboard_latest.html` | Glassmorphic dashboard (also embedded in the app) |
| `shadow_vs_official.md` | Champion-vs-shadow record (manual switch only) |
| `cleanup_log.csv` | What the retention step pruned |

Synthetic examples of the main shapes: [`examples/sample_output/`](examples/sample_output/).

## Data not included in this repository

No market data, price/delivery/option caches, validation history, evidence
bundles or portfolio files are published here. `data/` and `output/` are
git-ignored (see `desktop/.gitignore`) and are created on first run.

If you cloned an older snapshot that still tracked those folders, see
[`CONTRIBUTING.md`](CONTRIBUTING.md) — untrack them with `git rm --cached`,
never with `rm`.

## Back up your local history

Validation history accumulates one date at a time and cannot be regenerated
quickly. Back it up before upgrading:

```bash
cd desktop/nse_quant_engine
tar -czf ../nse_engine_backup_$(date +%Y%m%d).tgz data output
```

```powershell
cd desktop\nse_quant_engine
Compress-Archive -Path data,output -DestinationPath ..\nse_engine_backup.zip
```

## Tests

```bash
cd desktop/nse_quant_engine
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
python -m compileall -q .
```

Tests run **without network access**; data sources are mocked or read from
`tests/fixtures/`. CI runs this on Python 3.11 and 3.12.

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) first. Non-negotiables: no runtime
data committed, news stays context-only, ranking authority unchanged, adaptive
weighting stays dormant, and **new alphas require out-of-sample validation and
must not bypass survivor gates**.

Security reports: [`SECURITY.md`](SECURITY.md) (private GitHub advisory route).

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — pipeline, ranking authority, boundaries
- [`docs/VALIDATION_METHODOLOGY.md`](docs/VALIDATION_METHODOLOGY.md) — how a verdict is earned
- [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) — sources, limitations, backups
- [`desktop/nse_quant_engine/WORKFLOW.md`](desktop/nse_quant_engine/WORKFLOW.md) — the 16-step pipeline
- [`desktop/nse_quant_engine/INTEGRATION_GUIDE.md`](desktop/nse_quant_engine/INTEGRATION_GUIDE.md) — how modules bolt together
- [`desktop/nse_quant_engine/INSPIRATION_MAP.md`](desktop/nse_quant_engine/INSPIRATION_MAP.md) — concept → module → artifact
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) — dependency licenses
- [`CHANGELOG.md`](CHANGELOG.md)

## Web shell (optional)

```bash
bun install
bun run dev        # http://localhost:8080
bun run build
```

It renders a static project landing page only. No engine logic runs in the
browser.

## Credits

Professional-desk concepts (macro regime, sector/peer context, event calendar,
FII/DII + bulk-deals flow, multi-alpha IC survivorship, walk-forward backtest,
EV/Kelly, portfolio ship-gate, regime tilt, turnover-vs-cost rebalance diff,
portable LLM evidence bundle) were inspired by
[Fincept Terminal](https://github.com/Fincept-Corporation/FinceptTerminal) and
[Vibe Trading](https://github.com/HKUDS/Vibe-Trading). **No code from those
projects is bundled** — only concepts. Log lines for borrowed steps are
prefixed `[fincept]` or `[vibe]`.

## License

MIT — see [`LICENSE`](LICENSE).

## Disclaimer

Provided for research and educational use. **Not investment advice.** Markets
carry risk; past performance does not guarantee future results. You are solely
responsible for any decisions made using this software.
