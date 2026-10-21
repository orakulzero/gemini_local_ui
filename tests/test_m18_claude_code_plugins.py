import pytest
import json
from backend.app.mcp.server import MCPServer
from fastapi.testclient import TestClient
from backend.app.main import app

@pytest.mark.asyncio
async def test_mcp_claude_plugins_tools():
    server = MCPServer()

    # Test claude_list_plugins
    req_list = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "claude_list_plugins",
            "arguments": {}
        }
    }

    res_list = await server.handle_message(req_list)
    assert res_list.get("result") is not None
    assert res_list["result"]["isError"] is False
    assert "agents_count" in res_list["result"]["content"][0]["text"]

    # Test claude_sync_plugin_gems
    req_sync = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "claude_sync_plugin_gems",
            "arguments": {}
        }
    }

    res_sync = await server.handle_message(req_sync)
    assert res_sync.get("result") is not None
    assert res_sync["result"]["isError"] is False
    assert "synced_count" in res_sync["result"]["content"][0]["text"]


def test_rest_claude_plugins_endpoints():
    client = TestClient(app)

    # 1. GET /api/claude-plugins
    response = client.get("/api/claude-plugins")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert "skills_count" in data
    assert data["skills_count"] >= 1

    # 2. POST /api/claude-plugins/agents (create temporary agent)
    agent_data = {
        "id": "test-temp-agent",
        "name": "Test Temp Agent",
        "description": "Test description",
        "system_prompt": "Test prompt",
        "temperature": 0.7
    }
    response = client.post("/api/claude-plugins/agents", json=agent_data)
    assert response.status_code in [200, 201]

    # 3. DELETE /api/claude-plugins/agents/test-temp-agent
    response = client.delete("/api/claude-plugins/agents/test-temp-agent")
    assert response.status_code == 200

    # 4. POST /api/claude-plugins/sync-gems
    response = client.post("/api/claude-plugins/sync-gems")
    assert response.status_code == 200
    sync_data = response.json()
    assert "synced_count" in sync_data
