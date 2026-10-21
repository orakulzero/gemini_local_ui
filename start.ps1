$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "[Gemini Local UI] Starting backend server on http://127.0.0.1:8000 ..." -ForegroundColor Cyan
$BackendProc = Start-Process -FilePath "python" -ArgumentList "-m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000" -WorkingDirectory $ProjectRoot -PassThru

Write-Host "[Gemini Local UI] Starting frontend server on http://127.0.0.1:5173 ..." -ForegroundColor Cyan
$FrontendDir = Join-Path $ProjectRoot "frontend"
$FrontendProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c npm run preview -- --host 127.0.0.1 --port 5173" -WorkingDirectory $FrontendDir -PassThru

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "  Local Web UI:        http://127.0.0.1:5173" -ForegroundColor Green
Write-Host "  OpenAI API Endpoint: http://127.0.0.1:8000/v1" -ForegroundColor Green
Write-Host "  Press Enter to stop both servers..." -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Green
Read-Host

if (!$BackendProc.HasExited) { Stop-Process -Id $BackendProc.Id -Force -ErrorAction SilentlyContinue }
if (!$FrontendProc.HasExited) { Stop-Process -Id $FrontendProc.Id -Force -ErrorAction SilentlyContinue }
Write-Host "[Gemini Local UI] Stopped." -ForegroundColor Cyan
