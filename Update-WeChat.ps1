param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
$workspace = $PSScriptRoot
$extractDir = Join-Path $workspace 'wechat-extract'
$python = Join-Path $extractDir '.venv-pywxdump\Scripts\python.exe'
$extractor = Join-Path $extractDir 'run_pywxdump.py'
$importer = Join-Path $extractDir 'Import-WeChat.ps1'
$latest = Join-Path $extractDir 'pywxdump-latest.json'
$mutex = $null
$held = $false

try {
    foreach ($path in @($python, $extractor, $importer,
        (Join-Path $workspace 'chatlab-runtime\node-v22.23.2-win-x64\node.exe'),
        (Join-Path $workspace 'chatlab-runtime\app\node_modules\chatlab-cli\bin\chatlab.mjs'),
        (Join-Path $workspace 'Start-ChatLab.ps1'))) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "所需文件不存在：$path"
        }
    }
    if ($CheckOnly) {
        Write-Host '入口检查通过。未读取微信密钥，未提取或导入聊天记录。' -ForegroundColor Green
        return
    }

    # One updater per workspace. A named mutex leaves no persistent lock file.
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $hash = [BitConverter]::ToString($sha.ComputeHash(
            [System.Text.Encoding]::UTF8.GetBytes($workspace.ToLowerInvariant()))).Replace('-', '')
    }
    finally { $sha.Dispose() }
    $mutex = [System.Threading.Mutex]::new($false, "Local\ChatLabWeChatUpdate_$hash")
    try { $held = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $held = $true }
    if (-not $held) { throw '另一个微信更新任务正在运行，请等待它完成，不要重复启动。' }

    $previousOutput = $null
    if (Test-Path -LiteralPath $latest) {
        $previousOutput = (Get-Content -LiteralPath $latest -Raw -Encoding UTF8 | ConvertFrom-Json).output
    }
    Write-Host '第 1 步：使用开源 PyWxDump 提取微信“目标联系人”会话。' -ForegroundColor Cyan
    Write-Host '请先在电脑微信登录当前账号，并等待消息同步完成。'
    Write-Host '现在不要退出微信；看到下方“请退出微信”提示后，再从右下角托盘右键退出。'
    Write-Host '提取会重新读取本机历史；导入只追加新消息、跳过已有消息，不会修改微信原库。'
    Write-Host '请勿同时运行单独提取或导入入口，也不要关闭此窗口。'
    & $python -u $extractor | ForEach-Object {
        $line = $_
        Write-Host $line
        $event = $null
        try { $event = $line | ConvertFrom-Json } catch {}
        if ($event.stage -eq 'waiting_for_wechat_exit') {
            Write-Host '请退出微信：在右下角托盘右键微信，选择“退出微信”。仅关闭窗口无效。' -ForegroundColor Yellow
            Write-Host '请勿强制结束进程，等待上限为 30 分钟；退出后程序会自动继续。'
        }
        elseif ($event.stage -eq 'temporary_full_databases_removed') {
            Write-Host '原生提取完成，临时完整解密库已清理。现在可以重新打开微信。' -ForegroundColor Green
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "微信提取失败（退出代码 $LASTEXITCODE），未继续导入。请保留上方错误信息。" }
    $result = Get-Content -LiteralPath $latest -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $result.output -or $result.output -eq $previousOutput -or
        -not (Test-Path -LiteralPath $result.output -PathType Container)) {
        throw '没有检测到本次新生成的成功提取结果，停止导入，避免误用旧记录。'
    }
    Write-Host '第 2 步：转换、校验并增量导入原有微信会话，更新报告和索引。' -ForegroundColor Cyan
    & $importer
    Write-Host '微信更新完成。新增 0 条也属于正常结果；没有新增内容时已有记录保持不变。' -ForegroundColor Green
}
catch {
    Write-Host '微信更新未完成：' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host '请保留此窗口的错误信息。提取失败无需重新导入旧记录；若提取成功但导入失败，可单独重试“导入微信记录到ChatLab.cmd”。'
    exit 1
}
finally {
    if ($held) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
