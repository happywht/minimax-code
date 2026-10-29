@echo off
setlocal
rem Run from this release folder, even when opened from a shortcut.
pushd "%~dp0" || (
  echo Could not open the application folder. Move it to an accessible location.
  pause
  exit /b 1
)
where node >nul 2>&1
if errorlevel 1 (
  echo Node.js was not found on PATH. Prepare the application environment first.
  echo See docs\deployment.md. This launcher does not install software.
  popd
  pause
  exit /b 1
)
echo Starting MiniMax Code. Keep this window open while using the app.
echo Open the loopback address printed by the agent in your browser.
echo Closing this window may interrupt running tasks.
node scripts\start-production.mjs
set "launch_exit=%ERRORLEVEL%"
echo.
echo MiniMax Code stopped with exit code %launch_exit%.
echo If startup failed, read the messages above and see docs\deployment.md.
popd
pause
exit /b %launch_exit%
