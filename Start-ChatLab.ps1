param([switch]$NoOpen)

$ErrorActionPreference = 'Stop'

$workspace = $PSScriptRoot
$node = Join-Path $workspace 'chatlab-runtime\node-v22.23.2-win-x64\node.exe'
$cli = Join-Path $workspace 'chatlab-runtime\app\node_modules\chatlab-cli\bin\chatlab.mjs'
$dataDir = Join-Path $workspace 'chatlab-data'
$stdout = Join-Path $dataDir 'chatlab.stdout.log'
$stderr = Join-Path $dataDir 'chatlab.stderr.log'
$url = 'http://127.0.0.1:3110/'

function Get-LocalChatLabPage {
    $request = [System.Net.HttpWebRequest]::Create($url)
    $request.Proxy = $null
    $request.Timeout = 2000
    $request.ReadWriteTimeout = 2000
    $reply = $null
    $reader = $null
    try {
        $reply = $request.GetResponse()
        $reader = [System.IO.StreamReader]::new($reply.GetResponseStream())
        return [pscustomobject]@{StatusCode=[int]$reply.StatusCode;Content=$reader.ReadToEnd()}
    }
    finally {
        if ($reader) { $reader.Dispose() }
        if ($reply) { $reply.Dispose() }
    }
}

# A background launcher has no interactive terminal. Disable the CLI update
# prompt so a cached newer-version notice cannot block startup indefinitely.
$env:CHATLAB_SKIP_UPDATE_CHECK = '1'
$env:CHATLAB_DATA_DIR = $dataDir

if (-not (Test-Path -LiteralPath $node -PathType Leaf)) {
    throw "Node runtime not found: $node"
}
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) {
    throw "ChatLab CLI not found: $cli"
}
if (-not (Test-Path -LiteralPath $dataDir -PathType Container)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
}

$listener = Get-NetTCPConnection -LocalPort 3110 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($listener) {
    try {
        $response = Get-LocalChatLabPage
        if ($response.StatusCode -ne 200 -or $response.Content -notmatch '<title>ChatLab</title>') {
            throw 'Port 3110 is already used by another application.'
        }
    }
    catch {
        throw 'Port 3110 is already used, but the service is not ChatLab.'
    }
}
else {
    Start-Process `
        -FilePath $node `
        -ArgumentList @($cli, 'web', '--host', '127.0.0.1', '--port', '3110', '--no-open') `
        -WorkingDirectory $workspace `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdout `
        -RedirectStandardError $stderr

    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $response = Get-LocalChatLabPage
        }
        catch {
            $response = $null
        }
    } until (($response -and $response.StatusCode -eq 200) -or (Get-Date) -ge $deadline)

    if (-not $response -or $response.StatusCode -ne 200) {
        $details = if (Test-Path -LiteralPath $stderr) {
            (Get-Content -LiteralPath $stderr -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        }
        else {
            'No error log was created.'
        }
        throw "ChatLab did not start within 30 seconds.`n$details"
    }
}

# Resume the local semantic index and discover newly imported messages whenever
# ChatLab starts. Existing progress is preserved by ChatLab.
try {
    $semanticBase = "$($url.TrimEnd('/'))/_web/ai/semantic-index"
    # The pending-work endpoint checks live message counts before touching a
    # completed index. Unconditional /build may reprocess completed histories.
    Invoke-RestMethod `
        -Uri "$semanticBase/build-pending" `
        -Method Post `
        -ContentType 'application/json' `
        -Body '{}' `
        -TimeoutSec 15 | Out-Null
}
catch {
    # The web interface remains usable even if index resume is unavailable.
}

if (-not $NoOpen) { Start-Process $url }
