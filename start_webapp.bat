@echo off
REM ── Marquee dashboard launcher ──────────────────────────────────────
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment...
    python -m venv .venv
    echo [setup] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist ".env" (
    echo [error] .env not found. Copy .env.example to .env and set DISCORD_TOKEN
    echo         and WEBAPP_PASSWORD.
    pause
    exit /b 1
)

echo [run] Starting Marquee dashboard...
echo       Dashboard + bot + poller in one process.
echo       Do NOT also run start_bot.bat — two pollers will double-announce.
echo.
".venv\Scripts\python.exe" app.py
pause
