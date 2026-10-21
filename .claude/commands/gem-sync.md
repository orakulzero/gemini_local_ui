---
description: Synchronize .claude/agents/*.md plugin personas with Google Gemini Custom Gems and /api/claude-plugins
---

Synchronize all Claude Code subagent personas in `.claude/agents/*.md` with the Gemini Local UI Custom Gems registry:
1. Read `.claude/agents/*.md` and `.claude/commands/*.md`.
2. Call `POST /api/claude-plugins/sync-gems` or `mcp__gemini-local__gemini_create_gem` for each agent persona.
3. Verify via `pytest` that all plugin endpoints and Gem mappings are healthy.
