
# How to run the NSE Quant Engine on Windows

You don't install anything in Lovable for this — it's a local Python desktop app. The zip I shipped (`nse_quant_engine_v4_2_patched.zip`) is the whole project. Steps:

## One-time setup (≈ 3 minutes)

1. **Install Python 3.11 or 3.12** from python.org. On the first installer screen tick **"Add python.exe to PATH"**, then click Install.
2. **Unzip** `nse_quant_engine_v4_2_patched.zip` somewhere stable, e.g. `C:\Users\<you>\nse_quant_engine\`. You should see `run_app.py`, `run_app.bat`, `orchestrator.py`, and the `core\` folder at the top level.
3. **Install the Python libraries.** Open the unzipped folder in File Explorer, click the address bar, type `cmd`, press Enter — a Command Prompt opens already in that folder. Paste:
   ```
   python -m pip install --upgrade pip
   python -m pip install PySide6 pandas numpy yfinance requests beautifulsoup4 lxml openpyxl
   ```
   Wait for it to finish (one-time only).

## Every time you want to run it

- **Double-click `run_app.bat`** in the unzipped folder.
  - A small desktop window opens with a **Run** button, a live log pane, and tabs (Scores / Shadow / Compare / DQ Report / Validation / Trade Plan).
  - Click **Run**. It fetches NSE/AMFI/Yahoo data, scores stocks + ETFs, runs the v4.1 shadow engine in parallel, builds the validation, DQ, and trade-plan reports, and writes the champion-vs-shadow comparison. All outputs land in the `output\` subfolder and refresh in the tabs when done.
  - First run takes ≈ 3–6 minutes (network-bound). Subsequent runs with **"Skip fetch"** ticked finish in under a minute using cached data.

## If double-click does nothing

Open Command Prompt in the folder (address-bar `cmd` trick) and run:
```
python run_app.py
```
Any error will print there — usually it's a missing library, fixed by re-running the `pip install` line above.

## CLI alternative (no GUI)

Same folder, Command Prompt:
```
python orchestrator.py --all            # full pipeline
python orchestrator.py --all --skip-fetch   # re-score using cached data
```
Outputs go to `output\` exactly the same way.

## Where the results live

After a run, open the `output\` folder inside the project:
- `nse_quant_scores.xlsx` — official engine scores & ranks
- `nse_quant_scores_v4_shadow.xlsx` — shadow engine scores
- `trade_plan_report.md` + `.xlsx` — actionable trade plan
- `cross_sectional_validation_report.md` + `validation_status.json` — edge validation
- `dq_report.md` — data-quality health score and field fill rates
- `shadow_vs_official.md` — champion recommendation (manual switch, never auto)

## What I'd optionally add (say the word and I'll do it in build mode)

- A `QUICKSTART_WINDOWS.md` with the exact text above checked into the repo so it travels with the zip.
- A `setup_windows.bat` that runs the `pip install` line for you, so step 3 also becomes a double-click.

Want me to add those two convenience files?
