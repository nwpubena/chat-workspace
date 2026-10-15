param([switch]$NoOpen)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$workspace = $PSScriptRoot
$projectDir = Join-Path $workspace 'douyin-chat-export'
$python = Join-Path $projectDir '.venv\Scripts\python.exe'
$dataDir = Join-Path $projectDir 'data'
$stdout = Join-Path $dataDir 'panel.stdout.log'
$stderr = Join-Path $dataDir 'panel.stderr.log'
$url = 'http://127.0.0.1:8000/panel'

# Windows PowerShell 5.1 has no Invoke-WebRequest -NoProxy switch.
# The loopback health check must not go through a system HTTP proxy.
function Get-PanelResponse {
    $request = [System.Net.HttpWebRequest]::Create($url)
    $request.Proxy = $null
    $request.Timeout = 2000
    $request.ReadWriteTimeout = 2000
    $webResponse = $null
    $reader = $null
    try {
        $webResponse = $request.GetResponse()
        $reader = [System.IO.StreamReader]::new($webResponse.GetResponseStream(), [System.Text.Encoding]::UTF8)
        return [pscustomobject]@{ StatusCode = [int]$webResponse.StatusCode; Content = $reader.ReadToEnd() }
    }
    finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $webResponse) { $webResponse.Dispose() }
    }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python 运行环境不存在：$python"
}
if (-not (Test-Path -LiteralPath $dataDir -PathType Container)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}

$listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    try {
        $response = Get-PanelResponse
        if ($response.StatusCode -ne 200 -or $response.Content -notmatch '<title>Control Panel - 抖音聊天记录</title>') {
            throw '端口 8000 已被其他程序占用。'
        }
    }
    catch {
        throw "端口 8000 已被占用，但无法确认抖音面板正常响应。详细错误：$($_.Exception.Message)"
    }
}
else {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'

    Start-Process `
        -FilePath $python `
        -ArgumentList @('-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000') `
        -WorkingDirectory $projectDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr

    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $response = Get-PanelResponse
        }
        catch {
            $response = $null
        }
    } until (($response -and $response.StatusCode -eq 200) -or (Get-Date) -ge $deadline)

    if (-not $response -or $response.StatusCode -ne 200 -or $response.Content -notmatch '<title>Control Panel - 抖音聊天记录</title>') {
        $details = if (Test-Path -LiteralPath $stderr) {
            (Get-Content -LiteralPath $stderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        }
        else {
            '没有生成错误日志。'
        }
        throw "抖音聊天采集面板没有在 30 秒内启动。`n$details"
    }
}

Write-Host "抖音采集面板已启动：$url" -ForegroundColor Green
Write-Host '聊天浏览页面：http://127.0.0.1:8000/'
if (-not $NoOpen) {
    try { Start-Process $url }
    catch { Write-Warning "面板服务已就绪，但无法自动打开浏览器。请手动访问 $url" }
}
