@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0scripts\diagnose-release.ps1"
set "diagnose_exit=%ERRORLEVEL%"
echo.
echo Diagnostic finished with exit code %diagnose_exit%.
pause
exit /b %diagnose_exit%
