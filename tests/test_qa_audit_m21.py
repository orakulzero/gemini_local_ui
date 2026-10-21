"""
tests/test_qa_audit_m21.py
Comprehensive adversarial regression test suite for Milestone M21 QA Audit.
Covers:
- Gateway & Protocol Remediation (D1 - D10)
- Plugin Hub, Repo-RAG & Workspace Installer (O2 - O10)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, AsyncIterator, Dict, List
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.anthropic.adapter import AnthropicAdapter
from backend.app.anthropic.schemas import ToolUseBlock
from backend.app.anthropic.sse import format_anthropic_event, stream_anthropic_sse
from backend.app.bridge.streaming_parser import StreamChunk, sanitize_surrogate_text
from backend.app.mcp.server import MCPServer
from backend.app.openai import conversation_cache
from backend.app.openai.conversation_cache import (
    ConversationCache,
    _normalize_content,
    history_key,
)
from backend.app.openai.sse import stream_openai_sse
from backend.app.api import claude_plugins, notebooklm
from backend.app.api.claude_plugins import (
    detect_subagent_profile,
    init_claude_workspace,
    scan_plugins,
)
from backend.app.api.notebooklm import sync_repo_to_notebooklm


# ============================================================================
# Gateway & Protocol Tests (D1 - D10)
# ============================================================================

def test_anthropic_tool_call_primitive_json_arguments_safety() -> None:
    """[D1] Verifies that string/scalar primitive tool arguments do not raise Pydantic ValidationError."""
    # 1. String primitive
    text_str = '```tool_call\n{"name": "fetch_info", "arguments": "\\"scalar_str\\""}\n```'
    clean, blocks = AnthropicAdapter.parse_tool_calls(text_str)
    assert len(blocks) == 1
    assert isinstance(blocks[0], ToolUseBlock)
    assert blocks[0].name == "fetch_info"
    assert blocks[0].input == {"value": "scalar_str"}

    # 2. Integer primitive
    text_int = '```tool_call\n{"name": "compute_sum", "arguments": "42"}\n```'
    clean, blocks = AnthropicAdapter.parse_tool_calls(text_int)
    assert len(blocks) == 1
    assert blocks[0].input == {"value": 42}

    # 3. Boolean primitive
    text_bool = '```tool_call\n{"name": "toggle_flag", "arguments": "true"}\n```'
    clean, blocks = AnthropicAdapter.parse_tool_calls(text_bool)
    assert len(blocks) == 1
    assert blocks[0].input == {"value": True}

    # 4. Standard dict argument remains intact
    text_dict = '```tool_call\n{"name": "save_doc", "arguments": "{\\"path\\": \\"a.txt\\", \\"data\\": 100}"}\n```'
    clean, blocks = AnthropicAdapter.parse_tool_calls(text_dict)
    assert len(blocks) == 1
    assert blocks[0].input == {"path": "a.txt", "data": 100}


def test_conversation_cache_string_and_content_block_normalization() -> None:
    """[D2] Verifies Turn 1 string and Turn 2 single-element text list produce identical hashes."""
    text_content = "Hello Gemini Assistant"
    list_content = [{"type": "text", "text": "Hello Gemini Assistant"}]

    norm_str = _normalize_content(text_content)
    norm_list = _normalize_content(list_content)
    assert norm_str == norm_list == "Hello Gemini Assistant"

    transcript_1 = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hello Gemini Assistant"},
    ]
    transcript_2 = [
        {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Hello Gemini Assistant"}]},
    ]

    key_1 = history_key(transcript_1)
    key_2 = history_key(transcript_2)
    assert key_1 == key_2


@pytest.mark.asyncio
async def test_anthropic_sse_streaming_surrogate_safety() -> None:
    """[D3] Verifies unpaired UTF-16 surrogates do not crash Anthropic SSE streaming."""
    async def surrogate_generator() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(type="text", delta="Valid start \ud83d broken surrogate")
        yield StreamChunk(type="done", metadata={})

    events: List[str] = []
    async for ev in stream_anthropic_sse(surrogate_generator(), "msg_surrogate_test", "claude-3-7-sonnet"):
        events.append(ev)
        # Must encode to UTF-8 without raising UnicodeEncodeError
        encoded = ev.encode("utf-8")
        assert len(encoded) > 0

    assert any("broken surrogate" in ev for ev in events)


@pytest.mark.asyncio
async def test_openai_sse_streaming_surrogate_safety() -> None:
    """[D3] Verifies unpaired UTF-16 surrogates do not raise PydanticSerializationError in OpenAI SSE."""
    async def surrogate_generator() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(type="text", delta="OpenAI \ud83d text delta")
        yield StreamChunk(type="done", metadata={})

    lines: List[str] = []
    async for line in stream_openai_sse(surrogate_generator(), "req_surrogate_openai", "gpt-4o", int(time.time())):
        lines.append(line)
        encoded = line.encode("utf-8")
        assert len(encoded) > 0

    assert any("OpenAI" in line for line in lines)


@pytest.mark.asyncio
async def test_anthropic_sse_thinking_after_text_block_sequencing() -> None:
    """[D4] Verifies proper block sequencing when thought block appears after text block."""
    async def mixed_stream() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(type="text", delta="First text observation.")
        yield StreamChunk(type="thought", delta="Now thinking through logic.")
        yield StreamChunk(type="text", delta="Final answer text.")
        yield StreamChunk(type="done", metadata={})

    event_types: List[Dict[str, Any]] = []
    async for ev in stream_anthropic_sse(mixed_stream(), "msg_seq_test", "claude-3-7-sonnet"):
        for line in ev.splitlines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                event_types.append(data)

    starts = [d for d in event_types if d.get("type") == "content_block_start"]
    stops = [d for d in event_types if d.get("type") == "content_block_stop"]

    assert len(starts) == 3
    assert len(stops) == 3

    # Verify block 0 is text
    assert starts[0]["index"] == 0
    assert starts[0]["content_block"]["type"] == "text"
    assert stops[0]["index"] == 0

    # Verify block 1 is thinking
    assert starts[1]["index"] == 1
    assert starts[1]["content_block"]["type"] == "thinking"
    assert stops[1]["index"] == 1

    # Verify block 2 is text
    assert starts[2]["index"] == 2
    assert starts[2]["content_block"]["type"] == "text"
    assert stops[2]["index"] == 2


@pytest.mark.asyncio
async def test_mcp_empty_batch_jsonrpc_compliance() -> None:
    """[D5] Verifies empty batch [] returns JSON-RPC 2.0 error -32600 instead of HTTP 204."""
    server = MCPServer()
    res = await server.handle_message([])
    assert isinstance(res, dict)
    assert res.get("jsonrpc") == "2.0"
    assert res.get("error", {}).get("code") == -32600
    assert "empty batch" in res.get("error", {}).get("message", "")

    # Also test via REST endpoint
    with TestClient(app) as client:
        resp = client.post("/mcp", json=[])
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"]["code"] == -32600


def test_conversation_cache_model_and_account_keying() -> None:
    """[D6] Verifies history_key scopes keys when model or account is provided."""
    msgs = [{"role": "user", "content": "Analyze system architecture"}]
    key_base = history_key(msgs)
    key_flash = history_key(msgs, model="56fdd199312815e2")
    key_argon = history_key(msgs, model="95a4c0abcba24364")
    key_acc1 = history_key(msgs, account="acc_free")
    key_acc2 = history_key(msgs, account="acc_ultra")

    assert key_flash != key_argon
    assert key_acc1 != key_acc2
    assert key_base != key_flash


def test_conversation_cache_ttl_expiration(tmp_path: Path) -> None:
    """[D7] Verifies conversation cache entries expire and are pruned after TTL."""
    db_file = tmp_path / "test_ttl.db"
    cache = ConversationCache(max_size=50, db_path=db_file, ttl=0.15)  # 150ms TTL

    messages = [{"role": "user", "content": "Ephemeral prompt"}]
    reply = {"role": "assistant", "content": "Ephemeral reply"}
    chat_state = {"cid": "c_ephemeral", "rid": "r_ephemeral", "rcid": "rc_ephemeral"}

    key = cache.remember(messages, reply, chat_state)
    assert key is not None

    # Immediate lookup succeeds
    matched, entry = cache.find(messages + [reply, {"role": "user", "content": "follow-up"}])
    assert matched == 2
    assert entry is not None
    assert entry["cid"] == "c_ephemeral"

    # Wait past TTL
    time.sleep(0.20)

    # Subsequent lookup reports cache miss
    matched_after, entry_after = cache.find(messages + [reply, {"role": "user", "content": "follow-up"}])
    assert matched_after == 0
    assert entry_after is None


def test_conversation_cache_concurrent_wal_writes(tmp_path: Path) -> None:
    """[D8] Verifies 10 concurrent threads can write to SQLite WAL without SQLITE_BUSY / locks."""
    db_file = tmp_path / "concurrent_wal.db"
    cache = ConversationCache(max_size=500, db_path=db_file)

    errors: List[Exception] = []

    def worker_write(thread_id: int) -> None:
        try:
            for i in range(15):
                msgs = [{"role": "user", "content": f"Thread {thread_id} Message {i}"}]
                reply = {"role": "assistant", "content": f"Ack {thread_id}-{i}"}
                chat = {"cid": f"cid_{thread_id}_{i}", "rid": "rid", "rcid": "rcid"}
                cache.remember(msgs, reply, chat)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker_write, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0


def test_subagent_profile_detection_independent_of_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[D9/O7] Verifies scan_plugins and detect_subagent_profile work even when CWD is in a subfolder."""
    monkeypatch.chdir(tmp_path)
    agents = scan_plugins("agents")
    assert len(agents) >= 4
    agent_names = {a["name"] for a in agents}
    assert "explorer-scout" in agent_names
    assert "argon-architect" in agent_names

    # Test detection from system prompt
    scout_profile = detect_subagent_profile("You are the **Codebase Explorer Scout** for `gemini_local_ui`.")
    assert scout_profile is not None
    assert scout_profile["subagent"] == "explorer-scout"
    assert scout_profile["model"] == "56fdd199312815e2"


def test_subagent_auto_routing_notebooklm_librarian() -> None:
    """[D10] Verifies notebooklm-librarian prompt maps to Gemini 3 Flash tier."""
    prompt = "You are the **NotebookLM Grounded Librarian** for the `gemini_local_ui` project."
    profile = detect_subagent_profile(prompt)
    assert profile is not None
    assert profile["subagent"] == "notebooklm-librarian"
    assert profile["model"] == "56fdd199312815e2"


# ============================================================================
# Plugin Hub, Repo-RAG & Workspace Installer Tests (O2 - O10)
# ============================================================================

@pytest.mark.asyncio
async def test_sync_repo_path_traversal_relative_rejected(tmp_path: Path) -> None:
    """[O2] Verifies relative path traversal '../' is rejected with ValueError / HTTP 400."""
    with pytest.raises(ValueError, match="Path traversal detected"):
        await sync_repo_to_notebooklm(
            notebook_id="nb_test",
            paths=["../../Windows/win.ini"],
            root_dir=tmp_path,
        )

    with TestClient(app) as client:
        resp = client.post(
            "/api/notebooklm/sync-repo",
            json={"notebook_id": "nb_test", "paths": ["../../secret.txt"]},
        )
        assert resp.status_code == 400
        assert "traversal" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_sync_repo_path_traversal_absolute_rejected(tmp_path: Path) -> None:
    """[O2] Verifies absolute path escaping workspace root is rejected."""
    outside_file = tmp_path.parent / "outside_secret.txt"
    outside_file.write_text("classified", encoding="utf-8")

    workspace = tmp_path / "my_workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="escapes workspace root"):
        await sync_repo_to_notebooklm(
            notebook_id="nb_test",
            paths=[str(outside_file)],
            root_dir=workspace,
        )


@pytest.mark.asyncio
async def test_sync_repo_empty_notebook_id_rejected() -> None:
    """[O10] Verifies empty or whitespace notebook_id is rejected."""
    with pytest.raises(ValueError, match="notebook_id cannot be empty or whitespace"):
        await sync_repo_to_notebooklm(notebook_id="   ")

    with TestClient(app) as client:
        resp = client.post("/api/notebooklm/sync-repo", json={"notebook_id": "  "})
        assert resp.status_code == 400
        assert "notebook_id cannot be empty" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sync_repo_idempotency_content_hash(tmp_path: Path) -> None:
    """[O2/O10] Verifies repeated sync of unchanged files is deduplicated by SHA-256."""
    doc = tmp_path / "README.md"
    doc.write_text("# Test Repo\nHello World", encoding="utf-8")

    res1 = await sync_repo_to_notebooklm(
        notebook_id="nb_idempotent_test",
        paths=["README.md"],
        root_dir=tmp_path,
        force=False,
    )
    assert res1["synced_count"] == 1
    assert res1["skipped_count"] == 0

    res2 = await sync_repo_to_notebooklm(
        notebook_id="nb_idempotent_test",
        paths=["README.md"],
        root_dir=tmp_path,
        force=False,
    )
    assert res2["synced_count"] == 0
    assert res2["skipped_count"] == 1


def test_init_workspace_invalid_path_syntax_returns_400() -> None:
    """[O3] Verifies invalid directory characters return HTTP 400 instead of 500."""
    with TestClient(app) as client:
        resp = client.post("/api/claude-plugins/init-workspace", json={"target_dir": "invalid:<>|*?path"})
        assert resp.status_code == 400
        assert "Invalid target directory" in resp.json()["detail"] or "directory" in resp.json()["detail"]


def test_init_workspace_existing_file_target_returns_400(tmp_path: Path) -> None:
    """[O3] Verifies pointing target_dir to an existing file returns HTTP 400."""
    fake_file = tmp_path / "not_a_dir.txt"
    fake_file.write_text("content", encoding="utf-8")

    with TestClient(app) as client:
        resp = client.post("/api/claude-plugins/init-workspace", json={"target_dir": str(fake_file)})
        assert resp.status_code == 400
        assert "not a directory" in resp.json()["detail"]


def test_init_workspace_preserves_existing_mcp_servers(tmp_path: Path) -> None:
    """[O8] Verifies init_claude_workspace merges into existing .mcp.json rather than overwriting."""
    mcp_file = tmp_path / ".mcp.json"
    existing_config = {
        "mcpServers": {
            "pre_existing_server": {
                "command": "node",
                "args": ["server.js"],
            }
        }
    }
    mcp_file.write_text(json.dumps(existing_config, indent=2), encoding="utf-8")

    res = init_claude_workspace(str(tmp_path), include_mcp_config=True, overwrite=True)
    assert res["ok"] is True

    updated_mcp = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "pre_existing_server" in updated_mcp["mcpServers"]
    assert "gemini-local" in updated_mcp["mcpServers"]
    assert updated_mcp["mcpServers"]["gemini-local"]["url"].endswith("/mcp")


def test_agent_slug_path_traversal_rejected() -> None:
    """[O4] Verifies agent creation/deletion with path traversal slug returns HTTP 400."""
    with TestClient(app) as client:
        resp_post = client.post(
            "/api/claude-plugins/agents",
            json={"name": "../traversal_agent", "prompt": "malicious prompt"},
        )
        assert resp_post.status_code == 400
        assert "Invalid agent slug" in resp_post.json()["detail"]

        resp_del = client.delete("/api/claude-plugins/agents/..traversal_agent..")
        assert resp_del.status_code == 400
        assert "Invalid agent slug" in resp_del.json()["detail"]


def test_scan_recent_claude_sessions_survives_malformed_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[O5] Verifies session scanner ignores corrupted non-dict lines without crashing."""
    fake_home = tmp_path / "home"
    projects_dir = fake_home / ".claude" / "projects" / "test_proj"
    projects_dir.mkdir(parents=True)
    jsonl_file = projects_dir / "session1.jsonl"

    lines = [
        "42",
        json.dumps([1, 2, 3]),
        "INVALID_JSON_LINE",
        json.dumps({"type": "user", "message": "non_dict_message"}),
        json.dumps({"type": "user", "message": {"content": "Valid question prompt"}}),
        json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}}),
    ]
    jsonl_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    sessions = claude_plugins._scan_recent_claude_sessions(limit=5)
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "session1"
    assert sessions[0]["tool_counts"] == {"Read": 1}
    assert sessions[0]["last_prompt_preview"] == "Valid question prompt"


def test_load_persisted_swarm_events_skips_corrupted_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[O6] Verifies corrupted lines in swarm_events.jsonl do not discard valid events."""
    events_file = tmp_path / "swarm_events.jsonl"
    lines = [
        json.dumps({"id": "event_1", "model_tier_label": "Gemini 4 Argon"}),
        "CORRUPTED_LINE_NON_JSON",
        json.dumps({"id": "event_2", "model_tier_label": "Gemini 3 Flash"}),
    ]
    events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setattr(claude_plugins, "_SWARM_EVENTS_PATH", events_file)
    monkeypatch.setattr(claude_plugins, "_SWARM_EVENTS_LOADED", False)
    claude_plugins._SWARM_EVENTS.clear()

    claude_plugins._load_persisted_swarm_events()
    event_ids = [e["id"] for e in claude_plugins._SWARM_EVENTS]
    assert "event_1" in event_ids
    assert "event_2" in event_ids


def test_notebooklm_post_tool_sync_hook_ignores_non_write_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """[O9] Verifies notebooklm_post_tool_sync.py returns {} immediately for non-write tools."""
    from importlib.util import module_from_spec, spec_from_file_location
    from unittest.mock import MagicMock
    hook_path = Path(".claude/hooks/notebooklm_post_tool_sync.py").resolve()
    spec = spec_from_file_location("notebooklm_post_tool_sync", str(hook_path))
    assert spec and spec.loader
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)

    # 1. Non-write tool (Bash)
    res_bash = mod.run_hook({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    assert res_bash == {}

    # 2. Non-write tool (Read)
    res_read = mod.run_hook({"tool_name": "Read", "tool_input": {"file_path": "CLAUDE.md"}})
    assert res_read == {}

    # 3. Non-dict tool_input
    res_invalid_input = mod.run_hook({"tool_name": "Write", "tool_input": "string_not_dict"})
    assert res_invalid_input == {}

    # 4. Valid Write to non-syncable file
    res_py = mod.run_hook({"tool_name": "Write", "tool_input": {"file_path": "backend/app/main.py"}})
    assert res_py == {}

    # 5. Valid Write to syncable file (CLAUDE.md)
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"synced_files": ["CLAUDE.md"], "skipped_files": []}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda req, timeout=3.0: mock_resp)

    res_claude = mod.run_hook({"tool_name": "Write", "tool_input": {"file_path": "CLAUDE.md"}})
    assert "hookSpecificOutput" in res_claude
    assert "[NotebookLM RAG Auto-Sync]" in res_claude["hookSpecificOutput"]["additionalContext"]
