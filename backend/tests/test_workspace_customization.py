"""backend/tests/test_workspace_customization.py - M4 Workspace Customization test suite."""
from __future__ import annotations
import asyncio, json, os, tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.session import SessionManager
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.main import app
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository


@pytest.fixture
def workspace_context(tmp_path: Path):
    db = Database(tmp_path / "test_workspace.db")
    asyncio.run(db.init_schema(Path(__file__).parent.parent / "app" / "storage" / "schema.sql"))
    repo = ConversationRepository(db)
    sm = SessionManager(base_url="http://127.0.0.1:8999")
    sm.context.cookies = {"__Secure-1PSID": "mock_psid"}
    sm.context.access_token = "mock_token"

    import backend.app.main as main_mod
    main_mod.db, main_mod.repository, main_mod.session_manager = db, repo, sm
    app.state.db, app.state.repository, app.state.session_manager = db, repo, sm
    client = TestClient(app, base_url="http://testserver")
    yield client, repo, db, sm, tmp_path
    asyncio.run(db.close())


def parse_sse_events(response_text: str) -> List[Dict[str, Any]]:
    return [json.loads(l[5:].strip()) for l in response_text.splitlines() if l.strip().startswith("data:") and l[5:].strip()]


def test_global_prompts_crud_and_injection(workspace_context):
    client, repo, _, _, _ = workspace_context
    assert Path(tempfile.gettempdir()) / f"gemini_local_ui_pytest_{os.getpid()}" != Path(os.environ.get("LOCALAPPDATA", "")) / "gemini_local_ui"

    # GET presets & POST custom prompt
    resp = client.get("/api/workspace/prompts")
    assert resp.status_code == 200
    prompts = resp.json() if isinstance(resp.json(), list) else resp.json().get("prompts", resp.json().get("items", []))
    assert {"concise_engineer", "systems_architect", "direct_russian"}.issubset({p["id"] for p in prompts})

    c_resp = client.post("/api/workspace/prompts", json={"name": "Auditor", "description": "Audits", "content": "You are a strict security auditor.", "apply_mode": "turn0"})
    assert c_resp.status_code == 200
    prompt_id = c_resp.json()["id"]

    assert client.put(f"/api/workspace/prompts/{prompt_id}", json={"description": "Updated audits"}).status_code == 200
    assert client.post("/api/workspace/prompts/activate", json={"prompt_id": prompt_id, "enabled": True}).status_code == 200

    captured = []
    async def mock_stream(prompt, *a, **k):
        captured.append(prompt)
        yield StreamChunk(type="text", delta="Audit passed.")
        yield StreamChunk(type="done", delta="", metadata={"cid": "c1", "rid": "r1", "rcid": "rc1", "full_text": "Audit passed."})

    with patch.object(GeminiWebClient, "generate_stream", side_effect=mock_stream):
        gen_resp = client.post("/api/chat/generate", json={"prompt": "Inspect auth module", "model": "gemini-2.5-pro", "use_global_prompt": True})
    assert gen_resp.status_code == 200 and len(captured) == 1
    assert "strict security auditor" in captured[0] and "Inspect auth module" in captured[0]

    # Verify SQLite saved clean user prompt
    convs, _ = asyncio.run(repo.list_conversations())
    msgs = asyncio.run(repo.get_all_messages(convs[0].id))
    u_msg = next(m for m in msgs if m.role == "user")
    assert u_msg.content == "Inspect auth module" and "strict security auditor" not in u_msg.content
    assert client.delete(f"/api/workspace/prompts/{prompt_id}").status_code == 200


def test_skills_discovery_and_slash_trigger(workspace_context):
    client, repo, _, _, _ = workspace_context
    resp = client.get("/api/workspace/skills")
    assert resp.status_code == 200
    skills = resp.json() if isinstance(resp.json(), list) else resp.json().get("skills", resp.json().get("items", []))
    expected = {"architecture-blueprint", "code-reviewer-strict", "deep-research-synthesizer", "visual-explainer-canvas",
                "prompt-engineer-optimizer", "sql-data-analyst", "caveman-concise", "interactive-tutor-viva"}
    assert expected.issubset({s["id"] for s in skills})
    for s in skills:
        if s["id"] in expected:
            assert s.get("compatibility") == "native"

    # Custom skill CRUD
    c_resp = client.post("/api/workspace/skills", json={"name": "RustOpt", "description": "Opt", "compatibility": "native", "triggers": ["/rust-opt"], "content": "Zero allocs."})
    assert c_resp.status_code == 200
    cust_id = c_resp.json()["id"]
    assert client.delete(f"/api/workspace/skills/{cust_id}").status_code == 200

    # Slash trigger /caveman-concise
    captured = []
    async def mock_stream(prompt, *a, **k):
        captured.append(prompt)
        yield StreamChunk(type="text", delta="Talk short.")
        yield StreamChunk(type="done", delta="", metadata={"cid": "c2", "rid": "r2", "rcid": "rc2", "full_text": "Talk short."})

    with patch.object(GeminiWebClient, "generate_stream", side_effect=mock_stream):
        slash_resp = client.post("/api/chat/generate", json={"prompt": "/caveman-concise Explain quantum computing", "model": "gemini-2.5-pro"})
    assert slash_resp.status_code == 200 and len(captured) == 1
    assert "Explain quantum computing" in captured[0]

    # Verify SQLite stripped slash trigger
    convs, _ = asyncio.run(repo.list_conversations())
    msgs = asyncio.run(repo.get_all_messages(convs[0].id))
    u_msg = next(m for m in msgs if m.role == "user" and "quantum" in m.content)
    assert u_msg.content == "Explain quantum computing" and not u_msg.content.startswith("/caveman-concise")


def test_mcp_connectors_secret_masking_and_live_stdio(workspace_context):
    client, _, _, _, _ = workspace_context
    post_resp = client.post("/api/workspace/connectors", json={
        "name": "SecretMCP", "transport": "stdio", "command": "python", "args": ["-c", "print(1)"], "env": {"API_KEY": "super_secret_123"}, "enabled": True
    })
    assert post_resp.status_code == 200
    conn_id = post_resp.json()["id"]

    # Masking assertion
    get_resp = client.get("/api/workspace/connectors")
    assert get_resp.status_code == 200 and "super_secret_123" not in get_resp.text
    conns = get_resp.json() if isinstance(get_resp.json(), list) else get_resp.json().get("connectors", get_resp.json().get("items", []))
    target = next(c for c in conns if c["id"] == conn_id)
    assert target["env"]["API_KEY"] == "••••••••" and "API_KEY" in target.get("env_keys", [])
    client.delete(f"/api/workspace/connectors/{conn_id}")

    # Live stdio test on builtin_local_utils
    test_resp = client.post("/api/workspace/connectors/builtin_local_utils/test")
    assert test_resp.status_code == 200
    tools = test_resp.json().get("tools", [])
    assert any("calculate_expression" in (t["name"] if isinstance(t, dict) else t) for t in tools)

    call_resp = client.post("/api/workspace/connectors/builtin_local_utils/call", json={
        "namespaced_name": "builtin_local_utils__calculate_expression", "arguments": {"expression": "21 * 2"}
    })
    assert call_resp.status_code == 200 and "42" in str(call_resp.json().get("result", call_resp.text))


def test_chat_mcp_tool_calling_loop_end_to_end(workspace_context):
    client, repo, _, _, _ = workspace_context
    client.put("/api/workspace/connectors/builtin_local_utils", json={"enabled": True})

    call_count = 0
    async def mock_tool_stream(prompt, *a, **k):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield StreamChunk(type="text", delta="Calculating...")
            yield StreamChunk(type="done", delta="", metadata={
                "cid": "c_mcp", "rid": "r1", "rcid": "rc1",
                "full_text": "```tool_call\n{\"name\": \"builtin_local_utils__calculate_expression\", \"arguments\": {\"expression\": \"6 * 7\"}}\n```"
            })
        else:
            yield StreamChunk(type="text", delta="The answer is 42.")
            yield StreamChunk(type="done", delta="", metadata={"cid": "c_mcp", "rid": "r2", "rcid": "rc2", "full_text": "The answer is 42."})

    with patch.object(GeminiWebClient, "generate_stream", side_effect=mock_tool_stream):
        resp = client.post("/api/chat/generate", json={"prompt": "What is 6 * 7?", "model": "gemini-2.5-pro", "connector_ids": ["builtin_local_utils"]})
    assert resp.status_code == 200
    events = parse_sse_events(resp.text)
    assert any(e.get("type") == "tool_call" for e in events)

    done_event = next(e for e in events if e.get("type") == "done")
    assert "42" in done_event.get("metadata", {}).get("full_text", "")

    convs, _ = asyncio.run(repo.list_conversations())
    msgs = asyncio.run(repo.get_all_messages(convs[0].id))
    asst_msg = next(m for m in msgs if m.role == "assistant" and "42" in m.content)
    assert "tool_calls" in asst_msg.metadata
