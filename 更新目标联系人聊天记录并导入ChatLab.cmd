@echo off
chcp 65001 >nul
title Douyin Update and ChatLab Import
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-Douyin.ps1" %*
set "douyin_update_exit=%errorlevel%"
if /i "%~1"=="-CheckOnly" exit /b %douyin_update_exit%
if not "%douyin_update_exit%"=="0" echo Update failed. Please keep the error above for troubleshooting.
pause
exit /b %douyin_update_exit%
