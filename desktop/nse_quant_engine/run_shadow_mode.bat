@echo off
cd /d "%~dp0"

set PYTHON_EXE=.venv\Scripts\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

echo Running NSE Quant Engine Stage 4.0 Shadow Scoring...
echo Using: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" nse_quant_engine_v4_shadow.py
if errorlevel 1 goto fail

echo.
echo Done. Check:
echo output\latest_scores_v4_shadow.xlsx
echo output\latest_scores_v4_shadow.csv
echo output\shadow_mode_summary.json
pause
exit /b 0

:fail
echo.
echo Shadow mode failed. Review the error above.
pause
exit /b 1
