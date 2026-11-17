@echo off
rem Runs when the browser opens healthreport://start.
rem Security: any web page can open a healthreport:// link, so this file ignores whatever the link says.
rem It never uses an argument. It only starts the app that is in the parent folder.
setlocal
title Health report app
cd /d "%~dp0.."
if not defined APP_PORT set APP_PORT=8765

curl.exe --silent --max-time 2 --output nul http://127.0.0.1:%APP_PORT%/api/status
if not errorlevel 1 (
    echo The app is already running on port %APP_PORT%. Opening it in your browser.
    start "" "http://127.0.0.1:%APP_PORT%/"
    timeout /t 3 /nobreak >nul
    exit /b 0
)

rem On a fresh start the app opens the browser itself (see app.py), so nothing else is needed here.
set NO_BROWSER=

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" launch.py
    goto finished
)
where py >nul 2>&1
if not errorlevel 1 (
    py -3 launch.py
    goto finished
)
where python >nul 2>&1
if not errorlevel 1 (
    python launch.py
    goto finished
)
echo Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1

:finished
if errorlevel 1 pause
exit /b %errorlevel%
