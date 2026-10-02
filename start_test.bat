@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  Marquee  start (test mode)
REM  Runs the dashboard + bot + poller in one process.
REM  Use this INSTEAD of start_bot.bat, not alongside it.
REM ─────────────────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo  Virtual environment missing. Run setup_test.bat first.
    echo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo.
    echo  .env missing. Run setup_test.bat first.
    echo.
    pause
    exit /b 1
)

for /f "tokens=1,* delims==" %%a in ('findstr /b "WEBAPP_PASSWORD=" ".env"') do set "PW=%%b"

echo.
echo  ==========================================
echo   Marquee starting...
echo  ==========================================
echo.
echo   Dashboard:  http://127.0.0.1:8000
echo   Password:   %PW%
echo.
echo   Leave this window open. Ctrl+C to stop.
echo.
echo   Reminder: do NOT also run start_bot.bat.
echo.

REM Open the browser a moment after the server starts.
start "" /b cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"

".venv\Scripts\python.exe" app.py

echo.
echo  Bot stopped.
pause
