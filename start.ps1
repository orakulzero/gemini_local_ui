param(
    [switch]$UseSmartServer,
    [string]$SmartServerSshHost = "realme-wifi",
    [int]$EmbedPort = 8091,
    [int]$TelemetryPort = 18790
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TunnelProc = $null

function Test-LocalPortOpen {
    param([int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $ar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $ar.AsyncWaitHandle.WaitOne(400, $false) -and $client.Connected
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

if ($UseSmartServer -and -not (Test-LocalPortOpen -Port $EmbedPort)) {
    $ForwardSpec = "-L 127.0.0.1:${EmbedPort}:127.0.0.1:${EmbedPort}"
    if (-not (Test-LocalPortOpen -Port $TelemetryPort)) {
        $ForwardSpec += " -L 127.0.0.1:${TelemetryPort}:127.0.0.1:${TelemetryPort}"
    }
    Write-Host "[Gemini Local UI] Opening SSH tunnel to $SmartServerSshHost ($ForwardSpec) ..." -ForegroundColor Cyan
    $TunnelArgs = "-N -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes $ForwardSpec $SmartServerSshHost"
    $TunnelProc = Start-Process -FilePath "ssh" -ArgumentList $TunnelArgs -WindowStyle Hidden -PassThru
    for ($i = 0; $i -lt 15; $i++) {
        if (Test-LocalPortOpen -Port $EmbedPort) { break }
        Start-Sleep -Milliseconds 300
    }
    if (-not (Test-LocalPortOpen -Port $EmbedPort)) {
        Write-Host "[Gemini Local UI] WARN: Smart-server port $EmbedPort unreachable; RAG will degrade cleanly to FTS5 until phone connects." -ForegroundColor Yellow
    }
}

if ($UseSmartServer -or (Test-LocalPortOpen -Port $EmbedPort)) {
    $env:GEMINI_RAG_REMOTE_EMBED_URL = "http://127.0.0.1:${EmbedPort}/v1/embeddings"
    $env:GEMINI_RAG_THERMAL_TELEMETRY_URL = "http://127.0.0.1:${TelemetryPort}/api/telemetry"
    Write-Host "[Gemini Local UI] RAG Embeddings offloaded to smart-server ($($env:GEMINI_RAG_REMOTE_EMBED_URL))" -ForegroundColor Green
}

Write-Host "[Gemini Local UI] Starting backend server on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$BackendProc = Start-Process -FilePath "python" -ArgumentList "-m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $ProjectRoot -PassThru

Write-Host "[Gemini Local UI] Starting frontend server on http://127.0.0.1:5173 ..." -ForegroundColor Cyan
$FrontendDir = Join-Path $ProjectRoot "frontend"
$FrontendProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run preview -- --host 127.0.0.1 --port 5173" -WorkingDirectory $FrontendDir -PassThru

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Local Web UI:        http://127.0.0.1:5173" -ForegroundColor Green
Write-Host "  OpenAI API Endpoint: http://127.0.0.1:8000/v1" -ForegroundColor Green
if ($env:GEMINI_RAG_REMOTE_EMBED_URL) {
    Write-Host "  Remote Embedder:     $($env:GEMINI_RAG_REMOTE_EMBED_URL)" -ForegroundColor Green
}
Write-Host "  Press Enter to stop all servers..." -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Green
Read-Host

if ($BackendProc -and !$BackendProc.HasExited) { Stop-Process -Id $BackendProc.Id -Force -ErrorAction SilentlyContinue }
if ($FrontendProc -and !$FrontendProc.HasExited) { Stop-Process -Id $FrontendProc.Id -Force -ErrorAction SilentlyContinue }
if ($TunnelProc -and !$TunnelProc.HasExited) { Stop-Process -Id $TunnelProc.Id -Force -ErrorAction SilentlyContinue }
Write-Host "[Gemini Local UI] Stopped." -ForegroundColor Cyan
