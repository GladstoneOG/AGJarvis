@echo off
cd /d "%~dp0"
echo ====================================================
echo Starting Antigravity Discord Bridge & Jarvis Helper
echo ====================================================

:: Launch Antigravity Desktop App if not already running
tasklist /fi "imagename eq Antigravity.exe" 2>nul | find /i "Antigravity.exe" >nul
if errorlevel 1 (
    echo Launching Antigravity Desktop App...
    if exist "%LOCALAPPDATA%\Programs\Antigravity\Antigravity.exe" (
        start "" "%LOCALAPPDATA%\Programs\Antigravity\Antigravity.exe"
    ) else (
        echo [INFO] Antigravity not found in default LocalAppData path. Please ensure Antigravity is open.
    )
)

python bot.py
pause
