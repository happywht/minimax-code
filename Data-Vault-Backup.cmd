@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0scripts\data-vault-backup.ps1"
set "bundle_exit=%ERRORLEVEL%"
echo.
echo Data bundle command stopped with exit code %bundle_exit%.
pause
exit /b %bundle_exit%
