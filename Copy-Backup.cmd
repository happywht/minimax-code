@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0scripts\copy-backup.ps1"
set "copy_exit=%ERRORLEVEL%"
echo.
echo Backup copy command stopped with exit code %copy_exit%.
pause
exit /b %copy_exit%
