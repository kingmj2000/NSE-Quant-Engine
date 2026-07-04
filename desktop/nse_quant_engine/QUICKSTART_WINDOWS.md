# Quick Start — Windows

A local Python desktop app. No Lovable / browser involved.

## One-time setup (~3 min)

1. Install **Python 3.11 or 3.12** from https://python.org. On the first installer
   screen tick **"Add python.exe to PATH"**, then click Install.
2. Unzip this project anywhere stable, e.g. `C:\Users\<you>\nse_quant_engine\`.
   You should see `run_app.py`, `run_app.bat`, `orchestrator.py`, and the
   `core\` folder at the top level.
3. Double-click **`setup_windows.bat`** (installs the Python libraries).
   Or open Command Prompt in the folder and run it manually:
   ```
   python -m pip install --upgrade pip
   python -m pip install PySide6 pandas numpy yfinance requests beautifulsoup4 lxml openpyxl
   ```

## Every run

- **Double-click `run_app.bat`**.
- A desktop window opens with a **Run** button, a live log, and tabs
  (Scores / Shadow / Compare / DQ Report / Validation / Trade Plan).
- Click **Run**. First run ≈ 3–6 min (network). Tick **Skip fetch** afterwards
  to re-score from cached data in under a minute.

## If double-click does nothing

Open Command Prompt in the folder (click the address bar, type `cmd`, Enter):
```
python run_app.py
```
The error will print there — usually a missing library, fixed by re-running
`setup_windows.bat`.

## CLI (no GUI)

```
python orchestrator.py --all
python orchestrator.py --all --skip-fetch
```

## Outputs (in `output\`)

| File | What it is |
|---|---|
| `nse_quant_scores.xlsx` | Official engine scores & ranks |
| `nse_quant_scores_v4_shadow.xlsx` | Shadow engine scores |
| `trade_plan_report.md` / `.xlsx` | Actionable trade plan |
| `cross_sectional_validation_report.md` + `validation_status.json` | Edge validation (canonical JSON) |
| `dq_report.md` | Data-quality health score & field fill rates |
| `shadow_vs_official.md` | Champion-vs-shadow recommendation (manual switch only) |
