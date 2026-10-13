@echo off
title Threaded - BTC 15-Minute Analyzer
cd /d "%~dp0"
echo.
echo [Threaded] Running from: %~dp0
echo [Threaded] DEV_MODE=1 (local use skips login/paywall)
set DEV_MODE=1

echo Installing dependencies (first run only takes a minute)...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Could not install dependencies. Make sure Python 3.11+ is installed
    echo and added to PATH: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo.
echo Stopping any old copy of the analyzer still running on port 5000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING"') do (
    echo   - stopping stale process PID %%a
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo Starting the analyzer at http://127.0.0.1:5000 ...
start /b python server.py

echo Waiting for the server to come up...
for /l %%i in (1,1,30) do (
    timeout /t 1 /nobreak >nul
    netstat -ano | findstr ":5000 " | findstr "LISTENING" >nul
    if not errorlevel 1 goto :ready
)
echo [ERROR] The server did not start within 30 seconds. Check the messages above.
pause
exit /b 1

:ready
echo Server is up. Opening your browser...
timeout /t 1 /nobreak >nul
start "" "http://127.0.0.1:5000/app"
echo.
echo The analyzer is running. KEEP THIS WINDOW OPEN while you use it.
echo Close this window to stop the analyzer.
pause >nul
