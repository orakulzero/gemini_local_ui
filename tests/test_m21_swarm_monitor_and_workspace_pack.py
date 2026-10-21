"""
tests/test_m21_swarm_monitor_and_workspace_pack.py
Verification tests for Milestone M21:
1. Live Swarm Telemetry (`record_swarm_event` + `GET /api/claude-plugins/swarm-telemetry`)
2. 1-Click `.claude` Workspace Pack Installer (`POST /api/claude-plugins/init-workspace`)
3. MCP tool `claude_init_workspace` in `MCPServer`
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.api.claude_plugins import (
    _SWARM_EVENTS,
    _load_persisted_swarm_events,
    init_claude_workspace,
    record_swarm_event,
)
from backend.app.mcp.server import MCPServer


def test_swarm_telemetry_endpoint_and_event_recording():
    _load_persisted_swarm_events()
    _SWARM_EVENTS.clear()
    ev1 = record_swarm_event(
        message_id="msg_m21_flash_1",
        requested_model="claude-3-7-sonnet-20250219",
        resolved_model="56fdd199312815e2",
        subagent="explorer-scout",
        gem_id="gem_cc_explorer-scout",
        cache_hit=False,
        matched_turns=0,
        total_turns=1,
        tool_calls=["Glob", "Grep"],
        latency_ms=420,
        input_tokens=180,
        output_tokens=65,
        stream=True,
    )
    assert ev1["model_tier_label"] == "Gemini 3 Flash"
    assert ev1["subagent"] == "explorer-scout"

    ev2 = record_swarm_event(
        message_id="msg_m21_argon_2",
        requested_model="claude-3-7-sonnet-20250219",
        resolved_model="95a4c0abcba24364",
        subagent="argon-architect",
        gem_id="gem_cc_argon-architect",
        cache_hit=True,
        matched_turns=2,
        total_turns=3,
        tool_calls=["Skill", "mcp__gemini-local__notebooklm_sync_repo"],
        latency_ms=980,
        input_tokens=512,
        output_tokens=210,
        stream=True,
    )
    assert ev2["model_tier_label"] == "Gemini 4 Argon"

    client = TestClient(app)
    res = client.get("/api/claude-plugins/swarm-telemetry?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "cache_stats" in data
    assert data["cache_stats"]["total_recorded_turns"] >= 2
    assert data["cache_stats"]["cache_hit_rate_pct"] == 50.0
    assert data["tier_distribution"].get("Gemini 3 Flash", 0) >= 1
    assert data["tier_distribution"].get("Gemini 4 Argon", 0) >= 1
    assert data["subagent_distribution"].get("explorer-scout", 0) >= 1
    assert data["subagent_distribution"].get("argon-architect", 0) >= 1
    assert len(data["live_events"]) >= 2
    assert data["live_events"][0]["id"] == "msg_m21_argon_2"


def test_init_claude_workspace_rest_and_helper(tmp_path: Path):
    target_project = tmp_path / "sample_external_project"
    client = TestClient(app)
    res = client.post(
        "/api/claude-plugins/init-workspace",
        json={
            "target_dir": str(target_project),
            "include_mcp_config": True,
            "overwrite": True,
            "gateway_url": "http://127.0.0.1:8000",
        },
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["counts"]["agents"] >= 3
    assert payload["counts"]["skills"] >= 2
    assert payload["counts"]["commands"] >= 2
    assert payload["counts"]["hooks"] >= 1

    # Verify installed structure
    assert (target_project / ".claude" / "agents" / "explorer-scout.md").exists()
    assert (target_project / ".claude" / "agents" / "argon-architect.md").exists()
    assert (target_project / ".claude" / "skills" / "repo-rag" / "SKILL.md").exists()
    assert (target_project / ".claude" / "hooks" / "notebooklm_post_tool_sync.py").exists()
    assert (target_project / ".claude" / "settings.json").exists()
    assert (target_project / ".mcp.json").exists()

    settings_data = json.loads((target_project / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings_data["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8000"
    assert "PostToolUse" in settings_data["hooks"]

    mcp_data = json.loads((target_project / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp_data["mcpServers"]["gemini-local"]["url"] == "http://127.0.0.1:8000/mcp"


def test_mcp_claude_init_workspace_tool(tmp_path: Path):
    async def _run():
        mcp = MCPServer()
        list_resp = await mcp.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tool_names = [t["name"] for t in list_resp["result"]["tools"]]
        assert "claude_init_workspace" in tool_names

        dest = tmp_path / "mcp_initialized_workspace"
        call_resp = await mcp.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "claude_init_workspace",
                    "arguments": {
                        "target_dir": str(dest),
                        "include_mcp_config": True,
                    },
                },
            }
        )
        assert call_resp["result"]["isError"] is False
        parsed = json.loads(call_resp["result"]["content"][0]["text"])
        assert parsed["ok"] is True
        assert (dest / ".claude" / "settings.json").exists()
        assert (dest / ".mcp.json").exists()

    asyncio.run(_run())
