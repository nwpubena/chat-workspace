$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv-pywxdump\Scripts\python.exe'
$node = Join-Path $workspace 'chatlab-runtime\node-v22.23.2-win-x64\node.exe'
$cli = Join-Path $workspace 'chatlab-runtime\app\node_modules\chatlab-cli\bin\chatlab.mjs'
$sessionId = 'wechat_example_session'
$env:CHATLAB_DATA_DIR = Join-Path $workspace 'chatlab-data'
$env:CHATLAB_SKIP_UPDATE_CHECK = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $python (Join-Path $PSScriptRoot 'convert_pywxdump.py')
if ($LASTEXITCODE -ne 0) { throw 'Conversion failed; nothing imported.' }
$meta = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'chatlab-latest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
& $node $cli validate $meta.chatlab_file --json
if ($LASTEXITCODE -ne 0) { throw 'ChatLab validation failed; nothing imported.' }
& $node $cli import $meta.chatlab_file --session-id $sessionId --dry-run --json
if ($LASTEXITCODE -ne 0) { throw 'Import preview failed; nothing imported.' }
$base = 'http://127.0.0.1:3110/_web/ai/semantic-index'
$body = @{ sessionId = $sessionId } | ConvertTo-Json
$state = $null
try { $state = (Invoke-RestMethod -Uri "$base/status?sessionId=$sessionId" -TimeoutSec 5).status }
catch {
    # A listening but unresponsive server might still be writing the index.
    # Do not treat that case as "ChatLab is stopped" and import concurrently.
    $listener = Get-NetTCPConnection -LocalPort 3110 -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw 'ChatLab is listening but index status is unavailable. Restore the service before importing.' }
}
if ($state.running) {
    Write-Host 'Pausing this WeChat semantic index before import...'
    Invoke-RestMethod -Uri "$base/pause" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 30 | Out-Null
    $deadline = (Get-Date).AddMinutes(2)
    do {
        Start-Sleep -Seconds 2
        $state = (Invoke-RestMethod -Uri "$base/status?sessionId=$sessionId" -TimeoutSec 5).status
    } until (-not $state.running -or (Get-Date) -gt $deadline)
    if ($state.running) { throw 'Index is still busy; retry later. Nothing imported.' }
}
try {
    $importText = (& $node $cli import $meta.chatlab_file --session-id $sessionId --json | Out-String)
    if ($LASTEXITCODE -ne 0) { Write-Host $importText; throw 'ChatLab import failed.' }
    try { $importResult = $importText | ConvertFrom-Json }
    catch { throw 'Import returned unreadable JSON; inspect ChatLab before retrying.' }
    $data = $importResult.data
    if ($data.success -ne $true -or $null -eq $data.batch.errorCount -or
        $null -eq $data.newMessageCount -or $null -eq $data.duplicateCount -or
        $null -eq $data.session.totalCount) {
        throw 'Import result is incomplete or unsuccessful; no completion claimed.'
    }
    Write-Host "New messages: $($data.newMessageCount)" -ForegroundColor Green
    Write-Host "Existing messages skipped: $($data.duplicateCount)"
    Write-Host "WeChat session total: $($data.session.totalCount)"
    Write-Host "Import errors: $($data.batch.errorCount)"
    if ([int64]$data.batch.errorCount -ne 0) {
        throw 'Some messages failed to import; keep the native export and retry after investigating.'
    }
    & $python (Join-Path $PSScriptRoot 'make_baseline_report.py')
    if ($LASTEXITCODE -ne 0) { throw 'Report generation failed.' }
}
finally {
    if ($state.enabled) {
        try { Invoke-RestMethod -Uri "$base/build-pending" -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 15 | Out-Null } catch { Write-Warning 'Index resume failed; reopen ChatLab to retry.' }
    }
}
& (Join-Path $workspace 'Start-ChatLab.ps1')
Write-Host 'WeChat import complete. Existing messages were deduplicated.'
