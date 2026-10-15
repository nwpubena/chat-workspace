@echo off
chcp 65001 >nul
title Chat Workspace
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Control-Center.ps1" %*
set "control_center_exit=%errorlevel%"
if /i "%~1"=="-CheckOnly" exit /b %control_center_exit%
pause
exit /b %control_center_exit%
