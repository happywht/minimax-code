@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0scripts\start-release.ps1"
set "launch_exit=%ERRORLEVEL%"
echo.
echo Release launcher stopped with exit code %launch_exit%.
pause
exit /b %launch_exit%
