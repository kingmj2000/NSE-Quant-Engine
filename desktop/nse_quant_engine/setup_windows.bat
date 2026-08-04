@echo off
REM One-time environment setup for the NSE Quant Engine (Windows).
REM Creates .venv and installs exactly what requirements.txt declares.
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

echo Checking Python version (3.11 or 3.12 required)...
python -c "import sys; sys.exit(0 if sys.version_info[:2] in ((3,11),(3,12)) else 1)"
if errorlevel 1 (
    echo.
    python -c "import sys; print('Found Python ' + sys.version.split()[0])"
    echo This project requires Python 3.11 or 3.12.
    echo Install a supported version from https://python.org and re-run this file.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 goto :fail
) else (
    echo Reusing existing .venv
)

echo.
echo Upgrading pip inside the virtual environment...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo.
echo Installing project dependencies from requirements.txt (takes ~2 min)...
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo ====================================================
echo  Setup complete. Now double-click run_app.bat.
echo ====================================================
pause
exit /b 0

:fail
echo.
echo Setup failed. Scroll up to read the error, or run this file
echo from an Administrator Command Prompt.
pause
exit /b 1
