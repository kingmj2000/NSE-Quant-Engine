# Quick Start — Windows

A local Python desktop app. No Lovable / browser account involved.

## One-time setup (~3 min)

1. Install **Python 3.11 or 3.12** from https://python.org. On the first installer
   screen tick **"Add python.exe to PATH"**, then click Install.
2. Unzip this project anywhere stable, e.g. `C:\Users\<you>\nse_quant_engine\`.
   You should see `run_app.py`, `run_app.bat`, `orchestrator.py`, and the
   `core\` folder at the top level.
3. Double-click **`setup_windows.bat`**. It verifies the Python version, creates
   `.venv`, upgrades pip inside it and installs `requirements.txt`.
   The manual equivalent, from a Command Prompt in this folder:
   ```
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install --upgrade pip
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

## Every run

- **Double-click `run_app.bat`** — the desktop window runs the exact same
  `orchestrator.py` pipeline as the command line, just with a live log,
  an embedded dashboard and report tabs.
- Click **Run**. First run ≈ 3–6 min (network). Tick **Skip fetch** afterwards
  to re-score from cached data in under a minute.
- **📦 Evidence zip** reveals the newest `output\insight_bundle_<timestamp>.zip`.
  That bundle is built *after* the news step, so it always carries the current
  run's news and filings.

## If double-click does nothing

Open Command Prompt in the folder (click the address bar, type `cmd`, Enter):
```
.venv\Scripts\python.exe run_app.py
```
The error will print there — usually a missing library, fixed by re-running
`setup_windows.bat`.

## CLI (no GUI)

```
.venv\Scripts\python.exe orchestrator.py --all
.venv\Scripts\python.exe orchestrator.py --all --skip-fetch
```
`run_full_workflow.bat` is a thin wrapper around exactly these commands and
also accepts `--skip-fetch`.

## Outputs (in `output\`)

| File | What it is |
|---|---|
| `latest_scores.xlsx` | Official engine scores & ranks (Confidence_Adjusted_Score is authoritative) |
| `latest_scores_v4_shadow.xlsx` | Shadow engine scores (never authoritative) |
| `trade_plan_report.md` / `trade_plan_latest.xlsx` | Trade plan |
| `cross_sectional_validation_report.md` + `validation_status.json` | Edge validation (JSON is the canonical verdict) |
| `news_digest.json` / `news_market_context.md` | News & filings — human-review context only |
| `daily_changes.json` | Structured day-over-day rank/risk diff |
| `dq_report.md` | Data-quality health score & field fill rates |
| `shadow_vs_official.md` | Champion-vs-shadow running record (manual switch only) |
| `dashboard_latest.html` | Dashboard, also rendered inside the app window |
| `insight_bundle_<timestamp>.zip` | Evidence bundle for an external LLM |

Sheets named **Raw Score Diagnostic** and **Raw Score Low-Risk Diag** in the
workbook are diagnostic only — they are ordered by `Final_Score` and are not
the official ranking.
