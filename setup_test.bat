@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM  Marquee  quick test setup
REM  Run this once, then run start_test.bat
REM ─────────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  ==========================================
echo   Marquee  -^>  quick test setup
echo  ==========================================
echo.

REM ── 1. Python ────────────────────────────────────────────────────────────
where python >nul 2>nul
if errorlevel 1 (
    echo  [X] Python not found on PATH.
    echo      Install Python 3.11+ from https://python.org
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo  [1/4] %%v found

REM ── 2. Virtualenv + dependencies ─────────────────────────────────────────
if not exist ".venv\Scripts\python.exe" (
    echo  [2/4] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo  [X] Failed to create the virtual environment.
        pause
        exit /b 1
    )
) else (
    echo  [2/4] Virtual environment already exists
)

echo        Installing dependencies ^(first run takes a minute^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo  [X] Dependency install failed.
    echo      Try running manually:  .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)
echo        Dependencies installed

REM ── 3. .env ──────────────────────────────────────────────────────────────
if not exist ".env" (
    echo  [3/4] Creating .env with a generated password...
    copy /y ".env.example" ".env" >nul

    REM Generate a random-ish password so the dashboard has one out of the box.
    for /f "delims=" %%p in ('powershell -NoProfile -Command "-join ((48..57)+(97..122) | Get-Random -Count 16 | %%{[char]$_})"') do set "GENPW=%%p"
    if "!GENPW!"=="" set "GENPW=trailerbot"

    powershell -NoProfile -Command ^
      "(Get-Content '.env') -replace '^WEBAPP_PASSWORD=.*', 'WEBAPP_PASSWORD=!GENPW!' | Set-Content '.env'"
    echo        Dashboard password is:  !GENPW!
) else (
    echo  [3/4] .env already exists - leaving it alone
    for /f "tokens=1,* delims==" %%a in ('findstr /b "WEBAPP_PASSWORD=" ".env"') do set "GENPW=%%b"
)

REM ── 4. Discord token check ───────────────────────────────────────────────
set "HASTOKEN="
for /f "tokens=1,* delims==" %%a in ('findstr /b "DISCORD_TOKEN=" ".env"') do set "TOK=%%b"
if not "!TOK!"=="" if not "!TOK!"=="your-token-here" set "HASTOKEN=1"

echo  [4/4] Checking Discord token...
if not defined HASTOKEN (
    echo.
    echo  ---------------------------------------------------------------
    echo   ACTION NEEDED: add your Discord bot token
    echo  ---------------------------------------------------------------
    echo.
    echo   1. Open https://discord.com/developers/applications
    echo   2. New Application -^> name it -^> Bot tab -^> Reset Token
    echo   3. Copy the token
    echo   4. Paste it when Notepad opens
    echo.
    echo   Also on the Bot page, enable these two:
    echo     [x] Message Content Intent
    echo     [x] Server Members Intent
    echo.
    pause

    powershell -NoProfile -Command ^
      "$c = Get-Content '.env'; $t = Read-Host 'Paste your DISCORD_TOKEN';" ^
      "if ($t) { $c = $c -replace '^DISCORD_TOKEN=.*', ('DISCORD_TOKEN=' + $t); $c | Set-Content '.env'; Write-Host 'Token saved.' }" ^
      "else { Write-Host 'No token entered - you can add it to .env later.' }"
) else (
    echo        Token present
)

REM ── Done ─────────────────────────────────────────────────────────────────
echo.
echo  ==========================================
echo   Setup complete
echo  ==========================================
echo.
echo   Dashboard password:  !GENPW!
echo   Open:                http://127.0.0.1:8000
echo.
echo   Next:  run  start_test.bat
echo.
pause
