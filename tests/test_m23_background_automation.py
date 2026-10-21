from pathlib import Path
from fastapi.testclient import TestClient
from backend.app.main import app


def test_m23_background_batch_api_and_mcp(tmp_path: Path):
    client = TestClient(app)

    # 1. Verify /overnight-batch command is registered
    res_plugins = client.get("/api/claude-plugins")
    assert res_plugins.status_code == 200
    cmd_names = {c["name"] for c in res_plugins.json()["commands"]}
    assert "overnight-batch" in cmd_names

    # 2. Dispatch a synchronous background job via REST API
    stage_out = tmp_path / "stage1_out.md"
    payload = {
        "title": "Test M23 Overnight Synthesis Job",
        "workspace_dir": str(tmp_path),
        "run_async": False,
        "stages": [
            {
                "name": "s1-explore",
                "subagent": "code-explorer",
                "model": "haiku",
                "prompt": "Summarize key metrics for M23 test.",
                "output_file": str(stage_out),
            },
            {
                "name": "s2-architect",
                "subagent": "code-architect",
                "model": "opus",
                "prompt": "Synthesize architecture blueprint.",
            },
        ],
    }
    res_create = client.post("/api/claude-plugins/background-jobs", json=payload)
    assert res_create.status_code == 200
    body = res_create.json()
    assert body["ok"] is True
    job_id = body["job_id"]
    assert body["status"] == "completed"
    assert len(body["job"]["stage_results"]) == 2
    assert stage_out.exists()

    # 3. Query job status and list via REST API
    res_status = client.get(f"/api/claude-plugins/background-jobs/{job_id}")
    assert res_status.status_code == 200
    assert res_status.json()["job"]["job_id"] == job_id

    res_list = client.get("/api/claude-plugins/background-jobs")
    assert res_list.status_code == 200
    assert any(j["job_id"] == job_id for j in res_list.json()["jobs"])

    # 4. Verify MCP tool gemini_background_batch
    res_mcp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 901,
            "method": "tools/call",
            "params": {
                "name": "gemini_background_batch",
                "arguments": {
                    "action": "status",
                    "job_id": job_id,
                },
            },
        },
    )
    assert res_mcp.status_code == 200
    mcp_payload = res_mcp.json()["result"]
    assert mcp_payload["isError"] is False
    assert job_id in mcp_payload["content"][0]["text"]
