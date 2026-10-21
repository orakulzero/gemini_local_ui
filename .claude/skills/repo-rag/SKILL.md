---
name: repo-rag
description: Incrementally synchronize repository documentation, .claude specs, and architecture files into NotebookLM and query grounded RAG context with zero hallucinations.
---

# Repository-to-NotebookLM Grounded RAG (`repo-rag`)

When this skill is invoked:
1. Call `mcp__gemini-local__notebooklm_sync_repo` (default `notebook_id="nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f"`, `generate_briefing=True`) to incrementally sync `CLAUDE.md`, `README.md`, `.claude/agents/*.md`, and `.claude/skills/*/SKILL.md` with SHA-256 deduplication.
2. Query the grounded index via `mcp__gemini-local__notebooklm_query` instead of re-reading large markdown files into context.
3. Note that `.claude/hooks/notebooklm_post_tool_sync.py` automatically syncs modified documentation and `.claude/` files on every `Write` or `Edit` tool execution.
