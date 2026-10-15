param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'

[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$workspace = $PSScriptRoot
$exporterDir = Join-Path $workspace 'douyin-chat-export'
$python = Join-Path $exporterDir '.venv\Scripts\python.exe'
$extractScript = Join-Path $exporterDir 'extract.py'
$exportScript = Join-Path $exporterDir 'export.py'
$exportFile = Join-Path $exporterDir 'data\目标联系人_latest_export.jsonl'
$node = Join-Path $workspace 'chatlab-runtime\node-v22.23.2-win-x64\node.exe'
$chatlabCli = Join-Path $workspace 'chatlab-runtime\app\node_modules\chatlab-cli\bin\chatlab.mjs'
$chatlabData = Join-Path $workspace 'chatlab-data'
$chatlabLauncher = Join-Path $workspace 'Start-ChatLab.ps1'
$sessionId = 'douyin_example_session'
$chatlabUrl = 'http://127.0.0.1:3110/'
$semanticBase = 'http://127.0.0.1:3110/_web/ai/semantic-index'

function Write-Step([int]$Number, [string]$Text) {
    Write-Host ''
    Write-Host "========== 第 $Number 步：$Text ==========" -ForegroundColor Cyan
}

function Assert-File([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label 不存在：$Path"
    }
}

Assert-File $python 'Python 运行环境'
Assert-File $extractScript '抖音采集程序'
Assert-File $exportScript '聊天记录导出程序'
Assert-File $node 'ChatLab 运行环境'
Assert-File $chatlabCli 'ChatLab 程序'
Assert-File $chatlabLauncher 'ChatLab 启动程序'

if ($CheckOnly) {
    Write-Host '抖音更新入口检查通过。未启动浏览器，未采集或导入聊天记录。' -ForegroundColor Green
    return
}

$env:CHATLAB_SKIP_UPDATE_CHECK = '1'
$env:CHATLAB_DATA_DIR = $chatlabData

try {
    Write-Step 1 '从抖音增量采集“目标联系人”聊天记录'
    Write-Host '稍后会打开抖音网页。若页面要求登录，请在网页中完成登录。'
    Write-Host '采集期间不要关闭浏览器和这个窗口。已经保存过的消息不会重复写入。'
    Push-Location $exporterDir
    try {
        & $python -u $extractScript --incremental --filter '目标联系人' --download-images
        if ($LASTEXITCODE -ne 0) {
            throw "抖音采集失败，退出代码：$LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    Write-Step 2 '生成 ChatLab 导入文件'
    & $python -u $exportScript --filter '目标联系人' --format jsonl --output $exportFile
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $exportFile -PathType Leaf)) {
        throw '生成 ChatLab 导入文件失败。'
    }
    Write-Host "导出文件：$exportFile" -ForegroundColor DarkGray

    Write-Step 3 '暂停正在运行的语义索引'
    $chatlabRunning = $false
    try {
        $web = Invoke-WebRequest -UseBasicParsing -Uri $chatlabUrl -TimeoutSec 5
        if ($web.StatusCode -eq 200 -and $web.Content -match '<title>ChatLab</title>') {
            $chatlabRunning = $true
        }
        elseif ($web.StatusCode -eq 200) {
            throw '端口 3110 被其他程序占用，请先关闭那个程序。'
        }
    }
    catch {
        if ($_.Exception.Message -match '端口 3110') { throw }
    }

    if ($chatlabRunning) {
        try {
            $state = (Invoke-RestMethod -Uri "$semanticBase/status?sessionId=$sessionId" -Method Get -TimeoutSec 10).status
            if ($state.running) {
                $body = @{ sessionId = $sessionId } | ConvertTo-Json
                Invoke-RestMethod -Uri "$semanticBase/pause" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 15 | Out-Null

                $deadline = (Get-Date).AddMinutes(2)
                do {
                    Start-Sleep -Seconds 2
                    $state = (Invoke-RestMethod -Uri "$semanticBase/status?sessionId=$sessionId" -Method Get -TimeoutSec 10).status
                } while ($state.running -and (Get-Date) -lt $deadline)

                if ($state.running) {
                    throw '语义索引未能及时暂停。请稍后再运行本程序。'
                }
                Write-Host '语义索引已暂停，现有进度已经保存。'
            }
            else {
                Write-Host '语义索引当前没有运行，可以安全导入。'
            }
        }
        catch {
            throw "暂停语义索引失败：$($_.Exception.Message)"
        }
    }
    else {
        Write-Host 'ChatLab 当前未启动，直接进行导入。'
    }

    Write-Step 4 '增量导入 ChatLab'
    $importText = (& $node $chatlabCli import $exportFile --session-id $sessionId --json | Out-String)
    $importExitCode = $LASTEXITCODE
    if ($importExitCode -ne 0) {
        throw "ChatLab 导入失败，退出代码：$importExitCode"
    }

    try {
        $importResult = $importText | ConvertFrom-Json
        if (-not $importResult.data.success) {
            throw 'ChatLab 返回了未成功状态。'
        }
        $newCount = [int64]$importResult.data.newMessageCount
        $duplicateCount = [int64]$importResult.data.duplicateCount
        $totalCount = [int64]$importResult.data.session.totalCount
        $errorCount = [int64]$importResult.data.batch.errorCount

        Write-Host "新增消息：$newCount 条" -ForegroundColor Green
        Write-Host "已存在、自动跳过：$duplicateCount 条"
        Write-Host "ChatLab 当前总数：$totalCount 条"
        Write-Host "导入错误：$errorCount 条"
    }
    catch {
        Write-Host $importText
        throw "导入结果无法读取：$($_.Exception.Message)"
    }

    Write-Step 5 '打开 ChatLab 并继续语义索引'
    & $chatlabLauncher
    if ($LASTEXITCODE -ne 0) {
        throw '聊天记录已经导入，但 ChatLab 没有成功启动。请双击“启动ChatLab.cmd”重试。'
    }

    Write-Host ''
    Write-Host '全部操作成功。ChatLab 会在后台继续建立语义索引。' -ForegroundColor Green
    Write-Host '现在可以查看聊天记录；语义搜索和 RAG 会随着索引进度逐步恢复。'
}
catch {
    Write-Host ''
    Write-Host '本次更新没有完成：' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ''
    Write-Host '请保留这个窗口，并把窗口截图发给 Codex。'
    exit 1
}
