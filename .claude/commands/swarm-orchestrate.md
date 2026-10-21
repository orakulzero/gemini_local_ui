---
description: Execute a full multi-agent orchestration cycle (Explore + Argon Architect + Implement + Pytest + NotebookLM Sync)
---

Run a coordinated multi-agent workflow on `$ARGUMENTS`:
1. Delegate specification lookup or codebase scouting to subagents via `Agent` (`argon-architect` or `notebooklm-librarian`).
2. Implement the required code changes using `Edit` / `Write`.
3. Verify with `Bash` (`pytest`).
4. Sync the verified architectural summary to NotebookLM (`mcp__gemini-local__notebooklm_add_source`) and Custom Gems (`mcp__gemini-local__gemini_create_gem`).
