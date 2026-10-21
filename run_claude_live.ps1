$Host.UI.RawUI.WindowTitle = "Claude Code LIVE - M22 Architect Plugin Suite"
Set-Location $PSScriptRoot
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8000"
$env:ANTHROPIC_AUTH_TOKEN = "gemini-local-bridge"
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"
$env:CLAUDE_CODE_GIT_BASH_PATH = "C:\Program Files\Git\bin\bash.exe"

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "  CLAUDE CODE LIVE: M22 ARCHITECT PLUGIN SUITE                  " -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan

$prompt = "Perform a live M22 Architect Plugin verification: (1) call Skill tool with skill='architect'; (2) call MCP tool mcp__gemini-local__claude_sync_plugin_gems; (3) call Agent tool with subagent_type='code-explorer', description='Explore Architect files', prompt='Confirm .claude/skills/architect/SKILL.md exists in 1 sentence without calling extra tools'; (4) call Agent tool with subagent_type='code-architect', description='Draft EARS card', prompt='Write a 3-line EARS Architecture Card for M22 without calling tools'; (5) summarize the result in 4 short Russian bullet points."

& "claude" -p $prompt --dangerously-skip-permissions --verbose
Write-Host "`n[FINISHED M22 ARCHITECT LIVE SESSION]" -ForegroundColor Green
Start-Sleep -Seconds 3