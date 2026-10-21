# CLAUDE.md — Gemini Local UI & Multi-Protocol AI Gateway

## Project Root
`./`

## Architecture Map
- `backend/app/main.py` — FastAPI application root (`http://127.0.0.1:8000`)
- `backend/app/bridge/` — Google Gemini Web RPC bridge (`client.py`, `models.py`, `session.py`, `uploader.py`, `image_downloader.py`, `notebooklm.py`, `streaming_parser.py`)
- `backend/app/anthropic/` — Anthropic Messages API v1 Gateway (`/v1/messages`, `/v1/messages/count_tokens`, `adapter.py`, `sse.py`, `schemas.py`)
- `backend/app/openai/` — OpenAI Chat Completions Gateway (`/v1/chat/completions`, `conversation_cache.py`, `image_store.py`)
- `backend/app/mcp/` — Built-in Model Context Protocol Server (`POST /mcp` in `server.py` / `routes.py` + `stdio_server.py`)
- `backend/app/api/` — REST API routers (`gems.py`, `notebooklm.py`, `accounts.py`, `chat.py`, `admin.py`, `files.py`)
- `frontend/src/` — React + TypeScript + Vite UI (`http://127.0.0.1:5173`)
- `extension/` — Chrome MV3 Session & Cookie Sync Bridge

## Commands (Note: In Bash tool on Windows, always use forward slashes `"C:/Python314/python.exe"`)
- **Run Milestone Test Suite (M11–M17)**:
  `"C:/Python314/python.exe" -m pytest tests/test_m11_auth_hardening.py tests/test_m12_extension_sync.py tests/test_m13_argon_routing_upload_fife.py tests/test_m14_openai_stateful_tools_images.py tests/test_m15_anthropic_and_mcp.py tests/test_m16_gems_crud_and_extensions.py tests/test_m17_notebooklm_extended.py -q`
- **Run Bridge Unit Tests**:
  `"C:/Python314/python.exe" -m pytest backend/tests/test_bridge.py -q`
- **Build Frontend**:
  `cd frontend && npm run build`

## Connected MCP Tools (`mcp__gemini-local__*`)
- **Gemini 4 Argon & Images**: `gemini_argon_chat`, `gemini_argon_task`, `gemini_generate_image`
- **Custom Gems CRUD**: `gemini_list_gems`, `gemini_create_gem`, `gemini_update_gem`, `gemini_delete_gem`
- **NotebookLM RAG & Studio**: `notebooklm_list`, `notebooklm_query`, `notebooklm_add_source`, `notebooklm_add_url_source`, `notebooklm_generate_study_guide`, `notebooklm_generate_mind_map`, `notebooklm_generate_audio_overview`
- **M20 Repo-to-NotebookLM RAG**: Incremental SHA-256 sync via mcp__gemini-local__notebooklm_sync_repo, /repo-rag skill, and .claude/hooks/notebooklm_post_tool_sync.py.
