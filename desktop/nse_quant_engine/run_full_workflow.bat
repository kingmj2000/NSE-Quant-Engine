@echo off
cd /d "%~dp0"

set PYTHON_EXE=.venv\Scripts\python.exe

if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found.
    echo Run this first:
    echo python -m venv .venv
    echo .venv\Scripts\activate
    echo pip install -r requirements.txt
    pause
    exit /b 1
)

echo Running NSE Quant Engine Stage 3.5.10.1 Merged Metadata Workflow...
echo Using: %PYTHON_EXE%
echo.

echo Refreshing optional overlay feeds (FII/DII, bulk deals, fundamentals, earnings)...
"%PYTHON_EXE%" -c "from core.optional_data_fetchers import refresh_all; refresh_all()"
REM Non-fatal on purpose: pipeline continues even if a public source is down.

"%PYTHON_EXE%" universe_builder.py
if errorlevel 1 goto fail

echo.
"%PYTHON_EXE%" etf_quality_builder.py
if errorlevel 1 goto fail

echo.
echo Building NAV/AUM mapping before metadata fetches...
"%PYTHON_EXE%" etf_metadata_enricher.py
if errorlevel 1 goto fail

echo.
"%PYTHON_EXE%" etf_aum_auto_fetcher.py
if errorlevel 1 goto fail

echo.
"%PYTHON_EXE%" etf_ter_tracking_auto_fetcher.py
if errorlevel 1 goto fail

echo.
echo Rebuilding ETF metadata with safe AUM/TER/tracking imports...
"%PYTHON_EXE%" etf_metadata_enricher.py
if errorlevel 1 goto fail

echo.
echo Rebuilding ETF quality with enriched ETF metadata...
"%PYTHON_EXE%" etf_quality_builder.py
if errorlevel 1 goto fail

echo.
"%PYTHON_EXE%" nse_quant_engine.py
if errorlevel 1 goto fail

echo.
"%PYTHON_EXE%" validation_builder.py
if errorlevel 1 goto fail

echo.
"%PYTHON_EXE%" cross_sectional_validation.py
if errorlevel 1 goto fail

echo.
"%PYTHON_EXE%" trade_plan_builder.py
if errorlevel 1 goto fail

echo.
"%PYTHON_EXE%" news_market_builder.py
if errorlevel 1 goto fail

echo.
echo Done. Check:
echo output\cross_sectional_validation_report.md
echo output\latest_scores_validated.xlsx
echo output\trade_plan_latest.xlsx
echo output\trade_plan_report.md
echo output\news_market_context.md
echo data\etf_metadata_enriched.csv
echo data\etf_metadata_match_diagnostics.csv
echo data\etf_metadata_import_standardized.csv
echo data\etf_metadata_unresolved_review.csv
echo data\amfi_aum_source_standardized.csv
echo data\amfi_ter_tracking_source_standardized.csv
echo data\etf_aum_auto_fetch_log.csv
echo data\etf_ter_tracking_auto_fetch_log.csv
echo data\etf_ter_tracking_auto_debug_report.md
echo data\etf_metadata_imports\auto_amfi_aum_latest.csv
echo data\etf_metadata_imports\auto_amfi_ter_tracking_latest.csv
pause
exit /b 0

:fail
echo.
echo Workflow failed. Review the error above.
pause
exit /b 1
