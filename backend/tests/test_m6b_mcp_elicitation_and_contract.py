"""backend/tests/test_m6b_mcp_elicitation_and_contract.py - M6b MCP 2026-07-28, Elicitation & agy.stream.v1 tests."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.session import SessionManager
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.main import app, get_mcp_manager
from backend.app.mcp.elicitation_gate import ElicitationGate, get_elicitation_gate, validate_elicitation_url
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository


@pytest.fixture
def m6b_context(tmp_path: Path):
    import backend.app.main as main_mod
    orig = (main_mod.db, main_mod.repository, main_mod.session_manager)
    orig_st = (getattr(app.state, "db", None), getattr(app.state, "repository", None), getattr(app.state, "session_manager", None))
    db = Database(tmp_path / "test_m6b.db")
    asyncio.run(db.init_schema(Path(__file__).parent.parent / "app" / "storage" / "schema.sql"))
    repo = ConversationRepository(db)
    sm = SessionManager(base_url="http://127.0.0.1:8999")
    sm.context.cookies, sm.context.access_token = {"__Secure-1PSID": "mock_psid"}, "mock_token"
    main_mod.db, main_mod.repository, main_mod.session_manager = db, repo, sm
    app.state.db, app.state.repository, app.state.session_manager = db, repo, sm
    try:
        yield TestClient(app, base_url="http://127.0.0.1:8000"), repo
    finally:
        asyncio.run(db.close())
        main_mod.db, main_mod.repository, main_mod.session_manager = orig
        app.state.db, app.state.repository, app.state.session_manager = orig_st


def parse_sse_events(text: str) -> list[dict]:
    return [json.loads(line[5:].strip()) for line in text.splitlines() if line.strip().startswith("data:") and line[5:].strip()]


def test_mcp_2026_07_28_negotiation_discover_and_http_verbs(m6b_context):
    client, _ = m6b_context
    for req_id, params, expected_ver in [(1, {}, "2026-07-28"), (2, {"protocolVersion": "2026-07-28"}, "2026-07-28"), (3, {"protocolVersion": "2024-11-05"}, "2024-11-05")]:
        resp = client.post("/mcp/mcp", json={"jsonrpc": "2.0", "id": req_id, "method": "initialize", "params": params})
        assert resp.status_code == 200 and resp.json()["result"]["protocolVersion"] == expected_ver
        assert resp.headers.get("MCP-Protocol-Version") == expected_ver

    r_bad = client.post("/mcp/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}}, headers={"MCP-Protocol-Version": "1999-01-01"})
    assert r_bad.status_code == 400

    r_disc = client.post("/mcp/mcp", json={"jsonrpc": "2.0", "id": 5, "method": "server/discover", "params": {}})
    assert r_disc.status_code == 200
    disc_res = r_disc.json()["result"]
    assert disc_res["protocolVersion"] == "2026-07-28" and "elicitation" in disc_res["capabilities"]
    assert isinstance(disc_res["tools"], list) and len(disc_res["tools"]) > 0

    r_get = client.get("/mcp/mcp")
    assert r_get.status_code == 405 and "POST" in r_get.headers.get("Allow", "")
    assert client.delete("/mcp/mcp").status_code == 204


@pytest.mark.asyncio
async def test_elicitation_gate_url_safety_and_disconnect_abort():
    gate = ElicitationGate()
    for unsafe_url in ["javascript:alert(1)", "data:text/html,x", "file:///C:/win.ini"]:
        with pytest.raises(ValueError):
            validate_elicitation_url(unsafe_url)
        with pytest.raises(ValueError):
            gate.create_pending("eid_bad", connector_id="c1", mode="url", url=unsafe_url)

    for safe_url in ["https://auth.example.com/oauth", "http://127.0.0.1:9000/cb"]:
        assert validate_elicitation_url(safe_url) == safe_url
        gate.create_pending(f"eid_{safe_url[-4:]}", connector_id="c1", mode="url", url=safe_url)

    cancel_ev = asyncio.Event()
    gate.create_pending("eid_abort", connector_id="c1", mode="form", message="Input?")
    cancel_ev.set()
    t0 = time.monotonic()
    res = await gate.wait_for_response("eid_abort", timeout=120.0, cancel_event=cancel_ev)
    assert res == ("cancel", None) and (time.monotonic() - t0) < 0.5 and "eid_abort" not in gate._pending


def test_mrtr_elicitation_sse_turn_end_to_end(m6b_context):
    client, _ = m6b_context
    gate, mgr = get_elicitation_gate(), get_mcp_manager()

    gate.create_pending("eid_http_check", connector_id="builtin_local_utils", mode="form", message="Env?")
    assert client.post("/api/chat/elicitations/eid_http_check", json={"action": "accept", "content": {"env": "dev"}}).status_code == 200
    assert client.post("/api/chat/elicitations/eid_missing", json={"action": "cancel"}).status_code == 404

    seen_args: list[dict] = []

    async def fake_call_tool(name: str, args: dict, **kwargs):
        seen_args.append(dict(args))
        if len(seen_args) == 1:
            schema = {"type": "object", "properties": {"env": {"type": "string"}}, "required": ["env"]}
            payload = {"resultType": "input_required", "mode": "form", "message": "Select target env", "requestedSchema": schema}
            return {"ok": True, "is_error": False, "result": json.dumps(payload)}
        return {"ok": True, "is_error": False, "result": "Deployed to staging"}

    llm_turn = 0

    async def fake_llm_stream(prompt, *a, **kw):
        nonlocal llm_turn
        llm_turn += 1
        if llm_turn == 1:
            tc = '```tool_call\n{"name": "builtin_local_utils__calculate_expression", "arguments": {"expression": "deploy"}}\n```'
            yield StreamChunk(type="done", delta="", metadata={"cid": "c_m6b", "rid": "r1", "rcid": "rc1", "full_text": tc})
        else:
            assert "Deployed to staging" in prompt
            yield StreamChunk(type="text", delta="Deployment finished.")
            yield StreamChunk(type="done", delta="", metadata={"cid": "c_m6b", "rid": "r2", "rcid": "rc2", "full_text": "Deployment finished."})

    orig_create = gate.create_pending

    def auto_accept(eid: str, **kw):
        pending = orig_create(eid, **kw)
        asyncio.get_running_loop().call_soon(lambda: gate.submit_response(eid, "accept", {"env": "staging"}))
        return pending

    with patch.object(mgr, "call_tool", new=AsyncMock(side_effect=fake_call_tool)), patch.object(
        GeminiWebClient, "generate_stream", side_effect=fake_llm_stream
    ), patch.object(gate, "create_pending", side_effect=auto_accept):
        resp = client.post("/api/chat/generate", json={"prompt": "Deploy app", "model": "gemini-2.5-pro", "connector_ids": ["builtin_local_utils"]})

    assert resp.status_code == 200 and len(seen_args) == 2 and seen_args[1] == {"expression": "deploy", "env": "staging"}
    events = parse_sse_events(resp.text)
    assert any(e.get("type") == "elicitation_required" and e.get("metadata", {}).get("message") == "Select target env" for e in events)
    assert any(e.get("type") == "tool_call" and e.get("metadata", {}).get("tool_calls", [{}])[0].get("status") == "ok" for e in events)
    assert any(e.get("type") == "text" and "Deployment finished." in e.get("delta", "") for e in events)


def test_frozen_agy_stream_v1_contract_and_loopback_ws(m6b_context):
    client, _ = m6b_context
    r_contract = client.get("/api/chat/protocol-contract")
    assert r_contract.status_code == 200
    contract = r_contract.json()
    assert contract.get("protocol_version") == "agy.stream.v1"
    assert contract.get("methods") or contract.get("client_methods") or contract.get("schemas")

    with pytest.raises((WebSocketDisconnect, Exception)):
        with client.websocket_connect("/api/chat/ws", headers={"Origin": "null"}) as ws_bad:
            ws_bad.receive_json()

    async def ws_llm_stream(*a, **kw):
        yield StreamChunk(type="text", delta="WS Stream OK")
        yield StreamChunk(type="done", delta="", metadata={"cid": "c_ws", "rid": "r_ws", "rcid": "rc_ws", "full_text": "WS Stream OK"})

    with patch.object(GeminiWebClient, "generate_stream", side_effect=ws_llm_stream):
        with client.websocket_connect("/api/chat/ws", headers={"Origin": "http://127.0.0.1:5173"}) as ws:
            ws.send_json({"jsonrpc": "2.0", "id": "p1", "method": "ping", "params": {}})
            pong = ws.receive_json()
            assert pong["result"]["pong"] is True and pong["result"]["protocol_version"] == "agy.stream.v1"

            ws.send_json({"jsonrpc": "2.0", "id": "t1", "method": "session/prompt", "params": {"prompt": "Hi WS", "model": "gemini-2.5-flash"}})
            updates = []
            for _ in range(12):
                frame = ws.receive_json()
                if frame.get("method") == "session/update":
                    updates.append(frame["params"])
                    if frame["params"].get("event", {}).get("type") == "done":
                        break
            assert any(u.get("protocol_version") == "agy.stream.v1" and u.get("event", {}).get("type") == "text" for u in updates)
            assert any(u.get("event", {}).get("type") == "done" for u in updates)
