@echo off
chcp 65001 >nul
title Douyin Chat Panel
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Douyin-Panel.ps1"
if errorlevel 1 goto failed
pause
exit /b 0
:failed
echo.
echo Panel startup failed. Please keep the error above for troubleshooting.
pause
exit /b 1
