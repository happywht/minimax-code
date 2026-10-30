@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0scripts\manage-release-upgrade.ps1"
set "maintenance_exit=%ERRORLEVEL%"
echo.
echo Release maintenance stopped with exit code %maintenance_exit%.
pause
exit /b %maintenance_exit%
