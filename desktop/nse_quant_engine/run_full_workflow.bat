@echo off
REM Full pipeline run (no GUI). Delegates to the authoritative orchestrator so
REM this script can never drift out of sync with the real step list.
REM Usage:
REM   run_full_workflow.bat              full run (fetches fresh data)
REM   run_full_workflow.bat --skip-fetch re-score from cached data
setlocal
cd /d "%~dp0"

set PYTHON_EXE=.venv\Scripts\python.exe

if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found.
    echo Run setup_windows.bat once first.
    pause
    exit /b 1
)

echo Running the NSE Quant Engine pipeline via orchestrator.py --all %*
echo Using: %PYTHON_EXE%
echo.

"%PYTHON_EXE%" orchestrator.py --all %*
if errorlevel 1 goto fail

echo.
echo Done. Key outputs in output\:
echo   latest_scores.xlsx                    official scores and ranks
echo   latest_scores_v4_shadow.xlsx          shadow engine scores
echo   trade_plan_report.md                  trade plan
echo   cross_sectional_validation_report.md  validation report
echo   validation_status.json                canonical verdict
echo   news_digest.json                      news and filings (context only)
echo   dashboard_latest.html                 dashboard
echo   insight_bundle_^<timestamp^>.zip        AI evidence bundle
pause
exit /b 0

:fail
echo.
echo Workflow failed. Review the error above.
pause
exit /b 1
