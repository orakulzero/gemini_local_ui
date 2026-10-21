---
description: Launch an overnight or background multi-stage Gemini synthesis batch job (Gemini 4 Argon Task Mode + Gemini 3 Flash + NotebookLM)
---
Launch a background multi-stage synthesis job via `mcp__gemini-local__gemini_background_batch` (or `POST http://127.0.0.1:8000/api/claude-plugins/background-jobs`) for: $ARGUMENTS

Workflow:
1. Decompose the target task into parallel `explorer-scout` / `code-explorer` (`Gemini 3 Flash`) extraction stages and deep `code-architect` (`Gemini 4 Argon Task Mode`, `feature_mode=40`) synthesis stages.
2. Dispatch the background job with `action="create"` and optional `script_command` for DOCX/PDF typesetting or test verification.
3. Monitor progress via `mcp__gemini-local__gemini_background_batch(action="status", job_id=...)` or the **🤖 Claude Swarm** dashboard (`GET /api/claude-plugins/swarm-telemetry`).
