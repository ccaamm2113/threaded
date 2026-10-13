@echo off
title Stop Threaded
echo Looking for the analyzer on port 5000...
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5000 " ^| findstr "LISTENING"') do (
    echo Stopping process PID %%a
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)
if "%FOUND%"=="0" echo Nothing was running on port 5000.
echo.
echo Done. http://127.0.0.1:5000 should now be unreachable.
pause
