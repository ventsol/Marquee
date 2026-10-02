@echo off
REM ── Marquee launcher ────────────────────────────────────────────────
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment...
    python -m venv .venv
    echo [setup] Installing dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

if not exist ".env" (
    echo [error] .env not found. Copy .env.example to .env and set DISCORD_TOKEN.
    pause
    exit /b 1
)

echo [run] Starting Marquee... (Ctrl+C to stop)
".venv\Scripts\python.exe" bot.py
pause
