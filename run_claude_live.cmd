@echo off
chcp 65001 >nul
title Claude Code — Gemini Local UI (Live Task)
cd /d "%~dp0"
call claude --dangerously-skip-permissions "Run a complete project research and enhancement cycle: 1) Read CLAUDE.md and backend/app/api/gems.py (list_workspace_extensions endpoint) using the Read tool. 2) Add a count field (number of extensions) to the GET /api/extensions response using the Edit tool. 3) Run the pytest suite via Bash: python -m pytest tests/test_m16_gems_crud_and_extensions.py tests/test_m15_anthropic_and_mcp.py -q and verify all tests pass. 4) Record a concise summary of the enhancement in NotebookLM via mcp__gemini-local__notebooklm_add_source (notebook_id='nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f', title='Extensions Count & Failover Audit')."
