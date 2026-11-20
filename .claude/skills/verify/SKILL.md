---
name: verify
description: End-to-End, Property-Based & Differential Verification Harness for gemini_local_ui and agy-ultra-acp (Anthropic Verification Playbook). Run before every commit.
---

# Verification Skill (`verify`)

Follow Anthropic's verification principles strictly:
- **Never** declare "done" from unit tests alone; verify the real running app end-to-end.
- **Never** modify tests or `REVIEW.md` rules on the same branch to force a green build.
- **3-Round Escalation Rule**: If any check still fails after 3 consecutive fix rounds, stop and escalate to the maintainer with exact reproduction evidence.

## Stage 1 — Cheap Local Gates First (Fast Fail)
1. **Backend lint & OpenAPI parity**:
   ```powershell
   python -m ruff check backend
   python -u scripts/export_openapi.py --out $env:TEMP\openapi_check.json
   ```
2. **Backend Unit + Property (Hypothesis) + Differential Tests**:
   ```powershell
   python -u -m pytest backend/tests --timeout=60 -q
   ```
3. **Frontend TypeCheck, Unit Tests & Production Bundle**:
   ```powershell
   Set-Location frontend
   npx tsc --noEmit
   npx vitest run
   npm run build
   ```
4. **Rust (`agy-ultra-acp` + `src-tauri`) Tests & Zero-Warning Clippy**:
   ```powershell
   cargo test --quiet
   cargo clippy --all-targets -- -D warnings
   ```

## Stage 2 — Real Running System E2E Smoke (`:8000` + `:5173`)
Boot FastAPI (`OTEL_ENABLED=1`) on `127.0.0.1:8000` and Vite on `127.0.0.1:5173`, then verify:
1. **All safe parameterless `GET` routes** return 2xx (except optional uninstalled modules like `/api/flow/projects` 503), include `X-Trace-Id`, and leak 0 cookies/tokens.
2. **Adversarial Input Probes**:
   - `POST /api/rag/search` with `q="a"`, `"?"`, `" "`, `""" OR NOT ((` -> `200 OK` (zero 500s).
   - `POST /api/workspace/skills` with invalid slug `__bad__` -> `400` (zero 500s).
   - `POST /mcp/mcp` negotiates `2026-07-28`, `GET` -> `405`, `DELETE` -> `204`, bad `MCP-Protocol-Version` -> `400`.
   - `POST /api/acp/ping` -> `pong: true`, `engine: agy-ultra-acp`.
   - `ws://127.0.0.1:5173/api/chat/ws` (via Vite proxy!) accepts `ping` and rejects evil `Origin` with `403`.
3. **Real Browser User Flows (Playwright / Browser Subagent)**:
   - Zero uncaught `console.error`, `pageerror`, or unexpected HTTP `>= 400`.
   - Verify `SearchModal` (FTS5 + Hybrid RRF + `Escape` close + no XSS), `SettingsModal` (SSE <-> WS v1, Python <-> Rust ACP), `WorkspaceHubModal` (`builtin_local_utils` pin badge), `CanvasPanel`, `GemsManagerModal`, `AccountLimitsModal`, `NotebookLMDrawer`, `ClaudePluginsPanel`, `FlowStudioDrawer`, `MissionControlDrawer`.
   - Execute a live SSE turn (Code + KaTeX + Mermaid + Edit/Branch + Export) AND a live WebSocket v1 (`agy.stream.v1`) turn, verifying `saved` persistence and zero `OTel ContextVar` tracebacks in server logs, then delete only the temporary test chat.

## Stage 3 — Base-Branch Reliability Review
Check every changed file against `REVIEW.md` triggers from the base branch (`git show main:REVIEW.md`) and stamp the verification outcome (`Important` / `Should-Fix` / `Nit` / `Pre-existing`).
