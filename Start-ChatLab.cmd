@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-ChatLab.ps1"
if errorlevel 1 (
  echo.
  echo ChatLab failed to start. Please send a screenshot of this window.
  pause
)
