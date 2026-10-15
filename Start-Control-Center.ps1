param([switch]$NoOpen, [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$workspace = $PSScriptRoot
$python = Join-Path $workspace 'douyin-chat-export\.venv\Scripts\python.exe'
$appDir = Join-Path $workspace 'control-center'
$dataDir = Join-Path $appDir 'data'
$url = 'http://127.0.0.1:8765/'
foreach ($file in @($python, (Join-Path $appDir 'app.py'), (Join-Path $appDir 'web\index.html'))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "所需文件不存在：$file" }
}
if ($CheckOnly) { Write-Host '工作台入口检查通过，未启动服务或执行任何采集。'; return }

function Test-ControlCenter {
    $request = [System.Net.HttpWebRequest]::Create($url + 'health')
    $request.Proxy = $null
    $request.Timeout = 2000
    $request.ReadWriteTimeout = 2000
    $reply = $null
    $reader = $null
    try {
        $reply = $request.GetResponse()
        $reader = [IO.StreamReader]::new($reply.GetResponseStream())
        $status = $reader.ReadToEnd() | ConvertFrom-Json
        return $status.app -eq 'chat-workspace'
    }
    catch { return $false }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($reply) { $reply.Dispose() }
    }
}

if (-not (Test-ControlCenter)) {
    $listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
    if ($listener) { throw '8765 端口被其他服务占用或工作台无响应，请先核对，不要重复启动。' }
    if (-not (Test-Path -LiteralPath $dataDir)) { New-Item -ItemType Directory -Path $dataDir | Out-Null }
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app:app','--host','127.0.0.1','--port','8765') -WorkingDirectory $appDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $dataDir 'server.stdout.log') -RedirectStandardError (Join-Path $dataDir 'server.stderr.log')
    $deadline = (Get-Date).AddSeconds(30)
    do { Start-Sleep -Milliseconds 500; $ready = Test-ControlCenter } until ($ready -or (Get-Date) -gt $deadline)
    if (-not $ready) { throw "工作台未能启动，请查看 $dataDir\server.stderr.log" }
}
Write-Host "对话工作台已就绪：$url" -ForegroundColor Green
Write-Host '请在浏览器里选择操作，不要同时运行原有采集脚本。'
if (-not $NoOpen) {
    try { Start-Process $url }
    catch { Write-Warning "无法自动打开浏览器，请手动访问 $url" }
}
