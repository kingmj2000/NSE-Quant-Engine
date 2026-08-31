# Quickstart — Windows

Run the NSE Quant Engine on a fresh Windows machine in three steps.

Everything below is research tooling. Official mode is **watchlist only** unless
the validation gate in `output/validation_status.json` is positive.

## 1. Install Python

Install Python **3.11** or **3.12** from python.org (tick "Add python.exe to PATH").
Newer versions are not supported by the pinned scientific stack.

## 2. One-time setup

Double-click `setup_windows.bat` (or run it from a terminal in this folder).

It creates a local virtual environment and installs dependencies:

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Nothing is installed system-wide; delete `.venv` to undo the setup.

## 3. Run

| What you want | Do this |
| --- | --- |
| The desktop app | Double-click `run_app.bat` (runs `run_app.py`) |
| The full pipeline, headless | Double-click `run_full_workflow.bat` (runs `orchestrator.py --all`) |
| Pipeline without re-downloading prices | `run_full_workflow.bat --skip-fetch` |

The desktop app can also start the pipeline and refresh optional feeds from its
own buttons, so most users only need `run_app.bat`.

## Where the results land

All artifacts are written to `output/`:

| File | What it is |
| --- | --- |
| `latest_scores.xlsx` | Official workbook — scores, Top 5, diagnostics |
| `latest_scores.csv` | Flat scores for the same run |
| `trade_plan_latest.csv` | Official Top-5 trade plan |
| `validation_status.json` | The sole verdict authority for the run |
| `daily_changes.json` | What changed versus the previous run |
| `news_digest.json` | Human-review news and filings context |
| `latest_scores_v4_shadow.xlsx` | Shadow (experimental) engine workbook |
| `insight_bundle_*.zip` | Evidence bundle for AI/analyst review |

Ranking authority: `Confidence_Adjusted_Score` descending, `Symbol` ascending as
the tie-breaker. Any raw score shown in the workbook or UI is diagnostic only.

## Optional data feeds

Drop-in CSVs in `data/` (FII/DII flows, bulk deals, fundamentals) are refreshed
automatically when reachable. When a source is blocked from your machine, the run
continues and the affected panels say so instead of guessing.

## Troubleshooting

- **`python` not recognised** — reinstall Python with "Add python.exe to PATH".
- **Setup fails behind a corporate proxy** — set `HTTP_PROXY`/`HTTPS_PROXY` before
  running `setup_windows.bat`.
- **App opens but panels are empty** — run the pipeline once; the UI only reads
  files that `output/` already contains.
- **Feed warnings in the log** — expected when a provider blocks the machine; the
  run is still valid, with that context marked incomplete.
