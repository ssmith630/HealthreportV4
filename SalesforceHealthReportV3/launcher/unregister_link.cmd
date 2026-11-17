@echo off
rem Removes the healthreport:// link registered by register_link.cmd.
reg delete "HKCU\Software\Classes\healthreport" /f >nul 2>&1
if errorlevel 1 (
    echo The link was not registered, so there is nothing to remove.
) else (
    echo Removed. The launcher page can no longer start the app.
)
pause
