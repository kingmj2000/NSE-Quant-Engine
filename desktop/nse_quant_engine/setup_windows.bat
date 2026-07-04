@echo off
REM One-time dependency installer for the NSE Quant Engine.
REM Double-click this file once after unzipping the project.
setlocal
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo Python was not found on PATH.
    echo Install Python 3.11 or 3.12 from https://python.org
    echo and tick "Add python.exe to PATH" on the first installer screen.
    echo.
    pause
    exit /b 1
)

echo Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo.
echo Installing project dependencies (one-time, takes ~2 min)...
python -m pip install PySide6 PySide6-WebEngine pandas numpy yfinance requests beautifulsoup4 lxml openpyxl
if errorlevel 1 goto :fail

echo.
echo ====================================================
echo  Setup complete. Now double-click run_app.bat.
echo ====================================================
pause
exit /b 0

:fail
echo.
echo Dependency install failed. Scroll up to read the error,
echo or run this file from an Administrator Command Prompt.
pause
exit /b 1
