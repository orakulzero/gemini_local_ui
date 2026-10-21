import importlib.util
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.mcp.server import MCPServer
from backend.app.api.notebooklm import _REPO_SYNC_HASHES


def test_sync_repo_sha256_deduplication_and_briefing():
    _REPO_SYNC_HASHES.clear()
    with TestClient(app) as client:
        # 1. First sync (cold cache) -> uploads all matching files and generates briefing note
        resp1 = client.post(
            "/api/notebooklm/sync-repo",
            json={
                "notebook_id": "nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f",
                "generate_briefing": True,
                "force": False,
            },
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["ok"] is True
        assert data1["synced_count"] >= 5
        assert data1["skipped_count"] == 0
        assert "CLAUDE.md" in data1["synced_files"]
        assert ".claude/skills/repo-rag/SKILL.md" in data1["synced_files"]
        assert data1["briefing_note"] is not None
        assert data1["briefing_note"]["id"] == "note_repo_briefing"

        # 2. Second sync with identical file contents -> 100% SHA-256 deduplicated
        resp2 = client.post(
            "/api/notebooklm/sync-repo",
            json={
                "notebook_id": "nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f",
                "generate_briefing": False,
                "force": False,
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["ok"] is True
        assert data2["synced_count"] == 0
        assert data2["skipped_count"] == data1["synced_count"]


@pytest.mark.asyncio
async def test_mcp_notebooklm_sync_repo_tool():
    server = MCPServer()
    list_resp = await server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = [t["name"] for t in list_resp["result"]["tools"]]
    assert "notebooklm_sync_repo" in tools

    call_resp = await server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "notebooklm_sync_repo",
                "arguments": {
                    "notebook_id": "nb_2f3e4d5c-6b7a-8c9d-0e1f-2a3b4c5d6e7f",
                    "paths": [".claude/skills/repo-rag/SKILL.md"],
                    "generate_briefing": True,
                    "force": True,
                },
            },
        }
    )
    assert call_resp["result"]["isError"] is False
    payload = json.loads(call_resp["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["synced_files"] == [".claude/skills/repo-rag/SKILL.md"]
    assert payload["briefing_note"]["title"] == "Repository Architecture Briefing"


def test_post_tool_use_hook_filtering():
    hook_path = Path(".claude/hooks/notebooklm_post_tool_sync.py").resolve()
    assert hook_path.exists()
    spec = importlib.util.spec_from_file_location("notebooklm_post_tool_sync", hook_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    assert mod.should_sync_path("CLAUDE.md") is True
    assert mod.should_sync_path(".claude/skills/repo-rag/SKILL.md") is True
    assert mod.should_sync_path("backend/app/api/notebooklm.py") is True
    assert mod.should_sync_path("frontend/node_modules/pkg/index.js") is False
    assert mod.should_sync_path("scratch_debug.tmp") is False
