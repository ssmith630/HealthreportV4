@echo off
rem Registers the healthreport:// link for the current Windows user. No administrator rights needed.
rem Run it once, and again if you move the app folder. Undo it with unregister_link.cmd.
setlocal
set "HANDLER=%~dp0start_from_link.cmd"
if not exist "%HANDLER%" (
    echo Could not find start_from_link.cmd next to this file.
    pause
    exit /b 1
)

reg add "HKCU\Software\Classes\healthreport" /ve /d "URL:Health report" /f >nul || goto failed
reg add "HKCU\Software\Classes\healthreport" /v "URL Protocol" /d "" /f >nul || goto failed
reg add "HKCU\Software\Classes\healthreport\shell\open\command" /ve /d "\"%HANDLER%\"" /f >nul || goto failed

echo Done. The link healthreport://start now starts the app from:
echo   %HANDLER%
echo Open "Health report launcher.html" in your browser and click Start the app.
pause
exit /b 0

:failed
echo Could not write to the registry for the current user.
pause
exit /b 1
