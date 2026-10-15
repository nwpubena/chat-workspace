@echo off
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-WeChat.ps1"
set "wechat_update_exit=%errorlevel%"
pause
exit /b %wechat_update_exit%
