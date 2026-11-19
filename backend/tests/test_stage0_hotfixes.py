"""backend/tests/test_stage0_hotfixes.py - Stage 0 Critical Hotfixes test suite."""
from __future__ import annotations
import asyncio, json, ssl
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.session import SessionManager
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.main import app, get_mcp_manager
from backend.app.mcp.approval_gate import get_approval_gate
from backend.app.mcp.tool_fence_filter import ToolFenceFilter
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.workspace_crypto import decrypt_map, encrypt_map


@pytest.fixture
def stage0_context(tmp_path: Path):
    db = Database(tmp_path / "test_stage0.db")
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


def parse_sse_events(text: str) -> list[dict]:
    return [json.loads(l[5:].strip()) for l in text.splitlines() if l.strip().startswith("data:") and l[5:].strip()]


def test_tool_fence_filter_streaming_chunks():
    f = ToolFenceFilter()
    sample = "Привет, мир! Вот код:\n```python\nprint(123)\n```\nИ вызов инструмента:\n```tool_call\n{\"name\": \"x\", \"arguments\": {\"a\": 1}}\n```\nЗавершено."
    out, i, step = [], 0, 1
    while i < len(sample):
        out.append(f.feed(sample[i:i + step]))
        i += step
        step = (step % 3) + 1
    out.append(f.flush())
    res = "".join(out)
    assert "tool_call" not in res and '{"name": "x"' not in res
    assert "Привет, мир!" in res and "print(123)" in res and "Завершено." in res


def test_workspace_crypto_dpapi_roundtrip():
    data = {"API_KEY": "secret_987"}
    enc = encrypt_map(data, "k")
    assert enc.startswith("dpapi_v2:") and "secret_987" not in enc
    assert "secret_987" not in enc.replace("dpapi_v2:", "")
    assert decrypt_map(enc, "k") == data


@pytest.mark.asyncio
async def test_mcp_session_pool_warm_reuse():
    mgr = get_mcp_manager()
    counts = []
    for _ in range(5):
        res = await mgr.call_tool("builtin_local_utils__calculate_expression", {"expression": "6 * 7"})
        assert res.get("ok") is True
        counts.append(res.get("reuse_count", 0))
    assert sum(1 for c in counts if c > 0) >= 4 or counts[-1] >= 4
    if hasattr(mgr, "close_all"):
        await mgr.close_all()


@pytest.mark.asyncio
async def test_bridge_client_ssl_error_retry():
    cli = GeminiWebClient()
    h_cli = await cli._get_client()
    assert getattr(h_cli, "_transport", None) is not None

    attempts = 0
    class MockStream:
        def __init__(self, exc=None):
            self.exc = exc
        async def __aenter__(self):
            if self.exc:
                raise self.exc
            resp = AsyncMock(status_code=200)
            resp.aiter_bytes = AsyncMock(return_value=[b")]}'\n\n10\n[[\"wrb.fr\",null,\"[[null,[\\\"c\\\",\\\"r\\\",\\\"rc\\\"],null,null,[[\\\"rc\\\",[\\\"OK\\\"],null,null,null,null,null,null,[2]]]]]\"]]"])
            return resp
        async def __aexit__(self, *a):
            pass

    def mock_stream(*a, **k):
        nonlocal attempts
        attempts += 1
        return MockStream(ssl.SSLError("SSLV3_ALERT_BAD_RECORD_MAC")) if attempts == 1 else MockStream()

    with patch.object(h_cli, "stream", side_effect=mock_stream):
        chunks = []
        async for c in cli.generate_stream(prompt="Test SSL retry"):
            chunks.append(c)
        assert attempts >= 2 and any(c.type == "text" for c in chunks)


def test_hitl_approval_gate_allow_and_deny(stage0_context):
    client, repo, _, _, _ = stage0_context
    gate = get_approval_gate()

    # 1. Direct approval REST API test
    gate.create_pending("apr_allow_1", connector_id="builtin_local_utils", tool_name="calculate_expression", namespaced_name="builtin_local_utils__calculate_expression", arguments={"expression": "100 / 4"})
    r_allow = client.post("/api/chat/tool-approvals/apr_allow_1", json={"decision": "allow_once"})
    assert r_allow.status_code == 200 and r_allow.json()["decision"] == "allow_once"

    gate.create_pending("apr_deny_1", connector_id="builtin_local_utils", tool_name="calculate_expression", namespaced_name="builtin_local_utils__calculate_expression", arguments={"expression": "100 / 4"})
    r_deny = client.post("/api/chat/tool-approvals/apr_deny_1", json={"decision": "deny"})
    assert r_deny.status_code == 200 and r_deny.json()["decision"] == "deny"

    # 2. End-to-end chat flow: always_ask policy triggers tool_approval_required, user denies -> tool_call denied
    try:
        client.put("/api/workspace/connectors/builtin_local_utils", json={"enabled": True, "approval_policy": "always_ask"})

        call_count = 0
        async def mock_stream(prompt, *a, **k):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield StreamChunk(type="text", delta="Calling tool...")
                yield StreamChunk(type="done", delta="", metadata={
                    "cid": "c_gate", "rid": "r1", "rcid": "rc1",
                    "full_text": "```tool_call\n{\"name\": \"builtin_local_utils__calculate_expression\", \"arguments\": {\"expression\": \"100 / 4\"}}\n```"
                })
            else:
                yield StreamChunk(type="text", delta="Tool was denied.")
                yield StreamChunk(type="done", delta="", metadata={"cid": "c_gate", "rid": "r2", "rcid": "rc2", "full_text": "Tool was denied."})

        # Hook gate to auto-deny on create_pending during stream
        orig_create = gate.create_pending
        def auto_deny_hook(appr_id, **kw):
            ret = orig_create(appr_id, **kw)
            asyncio.get_event_loop().call_soon(lambda: gate.submit_decision(appr_id, "deny"))
            return ret

        with patch.object(GeminiWebClient, "generate_stream", side_effect=mock_stream), patch.object(gate, "create_pending", side_effect=auto_deny_hook):
            resp = client.post("/api/chat/generate", json={"prompt": "Eval 100/4", "model": "gemini-2.5-pro", "connector_ids": ["builtin_local_utils"]})
        assert resp.status_code == 200
        events = parse_sse_events(resp.text)
        assert any(e.get("type") == "tool_approval_required" for e in events)
        tool_events = [e for e in events if e.get("type") == "tool_call"]
        assert len(tool_events) >= 1
        assert any(tc.get("status") == "denied" for tc in tool_events[0].get("metadata", {}).get("tool_calls", []))
    finally:
        client.put("/api/workspace/connectors/builtin_local_utils", json={"enabled": True, "approval_policy": "auto_allow_readonly"})
