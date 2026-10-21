"""
tests/test_m16_gems_crud_and_extensions.py
Milestone M16 Verification Suite:
1. Full Custom Gems CRUD via batchexecute (oMH3Zd, kHv0Vd, UXcSJb, CNgdBe) in GeminiWebClient & /api/gems.
2. MCP Server Gems CRUD tools (gemini_create_gem, gemini_update_gem, gemini_delete_gem).
3. Model alias resolution with @gem=<id> and gem:<id> across OpenAI (/v1/chat/completions) and Anthropic (/v1/messages).
4. Native Google Workspace Extensions (@YouTube, @Gmail, @Google Drive, @Google Docs, @Google Flights, @Google Hotels, @Google Maps)
   in WorkspaceExtensionRegistry, 81-element StreamGenerate envelope, /api/extensions, OpenAI, Anthropic, and MCP.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient
import pytest

from backend.app.anthropic.adapter import AnthropicAdapter
from backend.app.anthropic.routes import (
    get_gemini_client as get_anthropic_client,
    get_gemini_uploader as get_anthropic_uploader,
)
from backend.app.api.gems import _LOCAL_CUSTOM_GEMS, get_optional_client
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import (
    Gem,
    GemSerializer,
    WorkspaceExtensionRegistry,
)
from backend.app.bridge.session import SessionContext
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.main import app
from backend.app.mcp.routes import get_gemini_client as get_mcp_gemini_client
from backend.app.openai.adapter import OpenAIAdapter
from backend.app.openai.routes import (
    get_gemini_client as get_openai_client,
    get_gemini_uploader as get_openai_uploader,
)


# ---------------------------------------------------------------------------
# 1. Unit Tests: GemSerializer & GeminiWebClient CRUD (oMH3Zd, kHv0Vd, UXcSJb)
# ---------------------------------------------------------------------------


def test_gem_serializer_crud_payloads() -> None:
    create_payload = GemSerializer.build_create_payload(
        name="Security Auditor",
        description="Audits Solidity & Rust",
        prompt="You are a senior security auditor.",
    )
    assert isinstance(create_payload, list) and len(create_payload) == 1
    assert create_payload[0][0] == "Security Auditor"
    assert create_payload[0][1] == "Audits Solidity & Rust"
    assert create_payload[0][2] == "You are a senior security auditor."

    parsed_id = GemSerializer.parse_create_response(["gem_sec_999"])
    assert parsed_id == "gem_sec_999"
    parsed_nested = GemSerializer.parse_create_response([["gem_nested_777", "meta"]])
    assert parsed_nested == "gem_nested_777"

    update_payload = GemSerializer.build_update_payload(
        gem_id="gem_sec_999",
        name="Updated Auditor",
        description="Updated desc",
        prompt="Updated prompt",
    )
    assert update_payload[0] == "gem_sec_999"
    assert update_payload[1][0] == "Updated Auditor"
    assert update_payload[1][2] == "Updated prompt"

    delete_payload = GemSerializer.build_delete_payload("gem_sec_999")
    assert delete_payload == ["gem_sec_999"]


@pytest.mark.asyncio
async def test_client_gems_crud_batchexecute_rpcs() -> None:
    ctx = SessionContext(
        cookies={"__Secure-1PSID": "test_psid"},
        access_token="SNlM0e_tok",
        session_id="12345",
    )
    client = GeminiWebClient(session_context=ctx)
    rpc_calls: List[tuple[str, Any]] = []

    async def fake_batchexecute(rpc_id: str, payload: List[Any]) -> Any:
        rpc_calls.append((rpc_id, payload))
        if rpc_id == "oMH3Zd":
            return ["gem_created_abc123"]
        if rpc_id == "kHv0Vd":
            return ["gem_created_abc123"]
        if rpc_id == "UXcSJb":
            return []
        if rpc_id == "CNgdBe":
            mode = payload[0]
            if mode == 2:
                return [[["gem_created_abc123", ["Code Reviewer", "Reviews PRs"], ["Be terse."]]]]
            return [[["coding-partner", ["Coding Partner", "System gem"], ["Help code."]]]]
        return None

    client.call_batchexecute = fake_batchexecute  # type: ignore[method-assign]

    # Create (oMH3Zd)
    created = await client.create_gem(
        name="Code Reviewer",
        prompt="Be terse.",
        description="Reviews PRs",
    )
    assert created.id == "gem_created_abc123"
    assert created.name == "Code Reviewer"
    assert created.prompt == "Be terse."
    assert rpc_calls[-1][0] == "oMH3Zd"

    # Get (CNgdBe)
    fetched = await client.get_gem("gem_created_abc123")
    assert fetched is not None
    assert fetched.id == "gem_created_abc123"
    assert fetched.name == "Code Reviewer"

    # Update (kHv0Vd)
    updated = await client.update_gem(
        gem_id="gem_created_abc123",
        name="Code Reviewer v2",
        prompt="Be extra terse.",
        description="Updated PR reviewer",
    )
    assert updated.id == "gem_created_abc123"
    assert updated.name == "Code Reviewer v2"
    assert rpc_calls[-1][0] == "kHv0Vd"
    assert rpc_calls[-1][1][0] == "gem_created_abc123"

    # Delete (UXcSJb)
    deleted = await client.delete_gem("gem_created_abc123")
    assert deleted is True
    assert rpc_calls[-1] == ("UXcSJb", ["gem_created_abc123"])


# ---------------------------------------------------------------------------
# 2. REST API Tests: /api/gems CRUD & /api/extensions
# ---------------------------------------------------------------------------


def test_rest_api_gems_crud_lifecycle() -> None:
    _LOCAL_CUSTOM_GEMS.clear()
    mock_client = AsyncMock(spec=GeminiWebClient)
    mock_client.create_gem.return_value = Gem(
        id="gem_rest_101",
        name="Rust Architect",
        description="Systems design",
        prompt="Design safe zero-copy systems.",
        is_builtin=False,
    )
    mock_client.update_gem.return_value = Gem(
        id="gem_rest_101",
        name="Rust Architect Pro",
        description="Async Tokio design",
        prompt="Design lock-free Rust systems.",
        is_builtin=False,
    )
    mock_client.delete_gem.return_value = True
    mock_client.list_gems.return_value = []

    app.dependency_overrides[get_optional_client] = lambda: mock_client
    try:
        tc = TestClient(app)
        # 1. Create Gem
        resp_create = tc.post(
            "/api/gems",
            json={
                "name": "Rust Architect",
                "description": "Systems design",
                "prompt": "Design safe zero-copy systems.",
            },
        )
        assert resp_create.status_code == 200
        data_create = resp_create.json()
        assert data_create["ok"] is True
        assert data_create["id"] == "gem_rest_101"
        assert data_create["title"] == "Rust Architect"

        # 2. Get Gem by ID
        resp_get = tc.get("/api/gems/gem_rest_101")
        assert resp_get.status_code == 200
        assert resp_get.json()["id"] == "gem_rest_101"

        # 3. Update Gem
        resp_upd = tc.put(
            "/api/gems/gem_rest_101",
            json={
                "title": "Rust Architect Pro",
                "description": "Async Tokio design",
                "system_prompt": "Design lock-free Rust systems.",
            },
        )
        assert resp_upd.status_code == 200
        assert resp_upd.json()["name"] == "Rust Architect Pro"
        assert resp_upd.json()["system_prompt"] == "Design lock-free Rust systems."

        # 4. Delete Gem
        resp_del = tc.delete("/api/gems/gem_rest_101")
        assert resp_del.status_code == 200
        assert resp_del.json() == {"ok": True, "deleted": True, "id": "gem_rest_101"}
    finally:
        app.dependency_overrides.pop(get_optional_client, None)
        _LOCAL_CUSTOM_GEMS.clear()


def test_rest_api_list_workspace_extensions() -> None:
    tc = TestClient(app)
    resp = tc.get("/api/extensions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    ext_ids = {e["id"] for e in body["extensions"]}
    assert {"youtube", "gmail", "google_drive", "google_docs", "google_flights", "google_hotels", "google_maps"}.issubset(ext_ids)


# ---------------------------------------------------------------------------
# 3. MCP Server Gems CRUD & Workspace Extensions Tests
# ---------------------------------------------------------------------------


def test_mcp_gems_crud_and_extensions_tools() -> None:
    mock_client = AsyncMock(spec=GeminiWebClient)
    mock_client.create_gem.return_value = Gem(
        id="gem_mcp_555",
        name="MCP Agent",
        description="Created via MCP",
        prompt="Execute MCP tasks.",
    )
    mock_client.update_gem.return_value = Gem(
        id="gem_mcp_555",
        name="MCP Agent v2",
        description="Updated via MCP",
        prompt="Execute v2 tasks.",
    )
    mock_client.delete_gem.return_value = True

    captured_stream_kwargs: Dict[str, Any] = {}

    async def fake_generate_stream(**kwargs: Any) -> AsyncIterator[StreamChunk]:
        captured_stream_kwargs.update(kwargs)
        yield StreamChunk(type="text", delta="Checked YouTube and Drive.")
        yield StreamChunk(type="done", delta="", metadata={"cid": "c_ext", "rid": "r_ext", "rcid": "rc_ext"})

    mock_client.generate_stream = fake_generate_stream

    app.dependency_overrides[get_mcp_gemini_client] = lambda: mock_client
    try:
        tc = TestClient(app)
        # Verify tools/list exposes the 3 new Gem CRUD tools
        resp_list = tc.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert resp_list.status_code == 200
        tool_names = {t["name"] for t in resp_list.json()["result"]["tools"]}
        assert {"gemini_create_gem", "gemini_update_gem", "gemini_delete_gem", "gemini_list_gems"}.issubset(tool_names)

        # Call gemini_create_gem
        resp_create = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "gemini_create_gem",
                    "arguments": {
                        "name": "MCP Agent",
                        "prompt": "Execute MCP tasks.",
                        "description": "Created via MCP",
                    },
                },
            },
        )
        assert resp_create.status_code == 200
        res_create = resp_create.json()["result"]
        assert res_create["isError"] is False
        assert "gem_mcp_555" in res_create["content"][0]["text"]

        # Call gemini_update_gem
        resp_upd = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "gemini_update_gem",
                    "arguments": {
                        "gem_id": "gem_mcp_555",
                        "name": "MCP Agent v2",
                        "prompt": "Execute v2 tasks.",
                    },
                },
            },
        )
        assert resp_upd.status_code == 200
        assert resp_upd.json()["result"]["isError"] is False
        assert "MCP Agent v2" in resp_upd.json()["result"]["content"][0]["text"]

        # Call gemini_delete_gem
        resp_del = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "gemini_delete_gem",
                    "arguments": {"gem_id": "gem_mcp_555"},
                },
            },
        )
        assert resp_del.status_code == 200
        assert resp_del.json()["result"]["isError"] is False
        assert '"deleted": true' in resp_del.json()["result"]["content"][0]["text"]

        # Call gemini_argon_chat with gem_id and extensions
        resp_chat = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "gemini_argon_chat",
                    "arguments": {
                        "prompt": "Find latest AI talks",
                        "gem_id": "gem_mcp_555",
                        "extensions": ["youtube", "google_drive"],
                    },
                },
            },
        )
        assert resp_chat.status_code == 200
        assert captured_stream_kwargs["gem_id"] == "gem_mcp_555"
        assert captured_stream_kwargs["extensions"] == ["youtube", "google_drive"]
    finally:
        app.dependency_overrides.pop(get_mcp_gemini_client, None)


# ---------------------------------------------------------------------------
# 4. Workspace Extension Normalization & 81-Element Envelope Tests
# ---------------------------------------------------------------------------


def test_workspace_extension_normalization_and_envelope() -> None:
    # Inline shorthand normalization (@drive -> @Google Drive, @docs -> @Google Docs, @youtube -> @YouTube)
    norm_text, active_ids = WorkspaceExtensionRegistry.normalize_prompt_and_extensions(
        "Check @drive and @docs plus @youtube for project specs",
        extensions=["gmail", "flights"],
    )
    assert "@Google Drive" in norm_text
    assert "@Google Docs" in norm_text
    assert "@YouTube" in norm_text
    assert norm_text.startswith("@Gmail @Google Flights ")
    assert set(active_ids) == {"google_drive", "google_docs", "youtube", "gmail", "google_flights"}

    # Verify 81-element envelope embeds normalized prompt and gem_id
    ctx = SessionContext(cookies={"__Secure-1PSID": "test"}, access_token="tok", session_id="sid")
    client = GeminiWebClient(session_context=ctx)
    env = client._build_81_element_envelope(
        prompt="Summarize my unread emails",
        gem_id="gem_custom_42",
        extensions=["gmail"],
    )
    assert env[0][0] == "@Gmail Summarize my unread emails"
    assert env[19] == "gem_custom_42"


# ---------------------------------------------------------------------------
# 5. Model Resolution with @gem=<id> and Gateway Integration (OpenAI & Anthropic)
# ---------------------------------------------------------------------------


def test_openai_and_anthropic_gem_model_resolution() -> None:
    # OpenAIAdapter
    m1, g1 = OpenAIAdapter.resolve_model("gemini-4-argon@gem=gem_alpha")
    assert m1 == "95a4c0abcba24364"
    assert g1 == "gem_alpha"

    m2, g2 = OpenAIAdapter.resolve_model("gemini-4-argon@gem=gem_beta@mode=task")
    assert m2 == "95a4c0abcba24364@mode=task"
    assert g2 == "gem_beta"

    m3, g3 = OpenAIAdapter.resolve_model("gem:gem_direct_99")
    assert m3 == "95a4c0abcba24364"
    assert g3 == "gem_direct_99"

    # AnthropicAdapter
    cm1, cg1 = AnthropicAdapter.resolve_model("claude-3-7-sonnet@gem=gem_claude_1")
    assert cm1 == "95a4c0abcba24364"
    assert cg1 == "gem_claude_1"

    cm2, cg2 = AnthropicAdapter.resolve_model("claude-opus-4@gem=gem_claude_2@mode=task")
    assert cm2 == "95a4c0abcba24364@mode=task"
    assert cg2 == "gem_claude_2"


def test_openai_and_anthropic_routes_forward_gem_and_extensions() -> None:
    captured_calls: List[Dict[str, Any]] = []

    class DummyClient:
        async def generate_stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
            captured_calls.append(kwargs)
            yield StreamChunk(type="text", delta="Workspace response ok.")
            yield StreamChunk(type="done", delta="", metadata={"cid": "c1", "rid": "r1", "rcid": "rc1"})

        async def close(self) -> None:
            pass

    dummy = DummyClient()
    app.dependency_overrides[get_openai_client] = lambda: dummy
    app.dependency_overrides[get_openai_uploader] = lambda: None
    app.dependency_overrides[get_anthropic_client] = lambda: dummy
    app.dependency_overrides[get_anthropic_uploader] = lambda: None
    try:
        tc = TestClient(app)
        # 1. OpenAI /v1/chat/completions with @gem= and extensions
        r_oai = tc.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-4-argon@gem=gem_oai_77",
                "messages": [{"role": "user", "content": "Search my Drive for Q3 report"}],
                "extensions": ["google_drive"],
            },
        )
        assert r_oai.status_code == 200
        assert captured_calls[-1]["gem_id"] == "gem_oai_77"
        assert captured_calls[-1]["extensions"] == ["google_drive"]

        # 2. Anthropic /v1/messages with @gem= and extensions
        r_ant = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-7-sonnet@gem=gem_ant_88",
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": "Find videos on MoE routing"}],
                "extensions": ["youtube"],
            },
        )
        assert r_ant.status_code == 200
        assert captured_calls[-1]["gem_id"] == "gem_ant_88"
        assert captured_calls[-1]["extensions"] == ["youtube"]
    finally:
        app.dependency_overrides.pop(get_openai_client, None)
        app.dependency_overrides.pop(get_openai_uploader, None)
        app.dependency_overrides.pop(get_anthropic_client, None)
        app.dependency_overrides.pop(get_anthropic_uploader, None)
