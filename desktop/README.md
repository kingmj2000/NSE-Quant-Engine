# Desktop app — NSE Quant Engine (v4.8)

This folder holds the PyQt/PySide6 desktop application. The Lovable web
preview on this project is unrelated — it exists only so the Python source
can live in a normal Lovable repository.

## Run it

```bash
cd desktop/nse_quant_engine
pip install -r requirements.txt
python run_app.py
```

Windows users can double-click `run_app.bat`.

## Why the app lives in Lovable

Historically every patch shipped as a fresh `nse_quant_engine_v4_X_patched.zip`,
which made it hard to see what actually changed between versions. Keeping the
source here means every patch is a normal diff in the Lovable code view (and
in GitHub if the project is connected). To run the latest build, download the
project ("Code Editor → Download codebase" or clone the GitHub repo) and run
`python run_app.py` inside this folder.

## Data / output are local

`data/` and `output/` are pipeline runtime folders and are intentionally not
committed here — the app creates them on first run.
