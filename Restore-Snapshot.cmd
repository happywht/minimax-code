@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0scripts\restore-snapshot.ps1"
set "restore_exit=%ERRORLEVEL%"
echo.
echo Restore command stopped with exit code %restore_exit%.
pause
exit /b %restore_exit%
