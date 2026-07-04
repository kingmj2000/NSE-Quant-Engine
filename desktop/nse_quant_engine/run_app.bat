@echo off
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo === NSE Quant Engine ===
echo Launching with: %PY%
echo Logs are captured in output\last_crash.log on any error.
echo.

"%PY%" run_app.py
set "EC=%ERRORLEVEL%"

echo.
if not "%EC%"=="0" (
  echo *** The app exited with error code %EC% ***
  if "%EC%"=="-1073741819" (
    echo Native Windows access violation detected. Check output\last_crash.log for faulthandler details.
  )
  if exist "output\last_crash.log" (
    echo ---- output\last_crash.log ----
    type "output\last_crash.log"
    echo -------------------------------
  )
) else (
  echo App closed normally.
)

echo.
echo Press any key to close this window . . .
pause >nul
endlocal
