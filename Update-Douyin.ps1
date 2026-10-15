param([switch]$CheckOnly)

# Keep CMD content ASCII-only; Windows PowerShell reads this file as UTF-8 BOM.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot '更新目标联系人聊天记录并导入ChatLab.ps1') -CheckOnly:$CheckOnly
