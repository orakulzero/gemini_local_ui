"""
tests/test_adversarial_m21_challenger_1.py
Empirical Adversarial Stress Verification Suite for Gateway & Protocol Endpoints.
Authored by m21_challenger_1.

Covers:
1. Malformed tool arguments (primitives, corrupted JSON, missing fields)
2. Unpaired UTF-16 surrogates in SSE streams (both OpenAI and Anthropic)
3. Thinking block interleaving and block sequencing in Anthropic SSE
4. Empty batch and malformed JSON-RPC calls on /mcp
5. High concurrency SQLite WAL writes in ConversationCache
6. Multi-turn prefix cache matching and collision immunity
7. Subagent auto-routing and MCP protocol compliance
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.anthropic.adapter import AnthropicAdapter
from backend.app.anthropic.schemas import ToolUseBlock
from backend.app.anthropic.sse import format_anthropic_event, stream_anthropic_sse
from backend.app.bridge.streaming_parser import StreamChunk, sanitize_surrogate_text
from backend.app.mcp.server import MCPServer
from backend.app.openai.adapter import OpenAIAdapter
from backend.app.openai.conversation_cache import (
    ConversationCache,
    history_key,
)
from backend.app.openai.sse import stream_openai_sse
from backend.app.api.claude_plugins import detect_subagent_profile


# ============================================================================
# Section 1: Malformed Tool Arguments Edge Cases
# ============================================================================

def test_anthropic_tool_call_corrupted_json_fences() -> None:
    """Verifies AnthropicAdapter ignores corrupted/unparseable JSON in fences without crashing."""
    corrupted_cases = [
        '```tool_call\n{ unclosed json\n```',
        '```tool_call\n\n```',
        '```tool_call\n   \n```',
        '```tool_call\nNone\n```',
        '```tool_call\n[{"name": "foo", "arguments": }]\n```',
        '```tool_call\n{"name": }\n```',
        '```tool_call\nNaN\n```',
        '```tool_call\nundefined\n```',
        '```json\n{"broken": true,\n```',
    ]
    for raw in corrupted_cases:
        clean, blocks = AnthropicAdapter.parse_tool_calls(raw)
        assert blocks == [], f"Expected 0 tool blocks for corrupted fence: {raw}"
        assert isinstance(clean, str)


def test_anthropic_tool_call_extreme_primitives() -> None:
    """Verifies scalar and non-dict primitives are safely wrapped into {'value': ...}."""
    test_cases: List[Tuple[str, Any]] = [
        ('```tool_call\n{"name": "f1", "arguments": 3.14159}\n```', 3.14159),
        ('```tool_call\n{"name": "f2", "arguments": -99999}\n```', -99999),
        ('```tool_call\n{"name": "f3", "arguments": true}\n```', True),
        ('```tool_call\n{"name": "f4", "arguments": false}\n```', False),
        ('```tool_call\n{"name": "f5", "arguments": null}\n```', None),
        ('```tool_call\n{"name": "f6", "arguments": [1, 2, "three", null]}\n```', [1, 2, "three", None]),
        ('```tool_call\n{"name": "f7", "arguments": "{\\"unclosed\\"}\"}\n```', {"raw_input": '{"unclosed"}'}),
    ]

    for raw, expected_val in test_cases:
        clean, blocks = AnthropicAdapter.parse_tool_calls(raw)
        assert len(blocks) == 1, f"Failed parsing block for: {raw}"
        assert isinstance(blocks[0], ToolUseBlock)
        assert blocks[0].id.startswith("toolu_")
        if isinstance(expected_val, dict) and "raw_input" in expected_val:
            assert blocks[0].input == expected_val
        else:
            assert blocks[0].input == {"value": expected_val}


def test_anthropic_tool_call_missing_and_invalid_names() -> None:
    """Verifies tool calls missing valid 'name' attributes are rejected."""
    invalid_name_cases = [
        '```tool_call\n{"arguments": {"a": 1}}\n```',
        '```tool_call\n{"name": "", "arguments": {"a": 1}}\n```',
        '```tool_call\n{"name": null, "arguments": {"a": 1}}\n```',
        '```tool_call\n{"name": false, "arguments": {"a": 1}}\n```',
    ]
    for raw in invalid_name_cases:
        clean, blocks = AnthropicAdapter.parse_tool_calls(raw)
        assert len(blocks) == 0, f"Expected rejection for missing/invalid name: {raw}"

    # Integer name converted to string
    raw_int_name = '```tool_call\n{"name": 404, "arguments": {"code": 1}}\n```'
    clean, blocks = AnthropicAdapter.parse_tool_calls(raw_int_name)
    assert len(blocks) == 1
    assert blocks[0].name == "404"
    assert blocks[0].input == {"code": 1}


def test_anthropic_tool_call_batch_mixed_valid_and_invalid() -> None:
    """Verifies array tool_call handling in a single fence."""
    raw_invalid_item = (
        '```tool_call\n'
        '[\n'
        '  {"name": "valid_tool_1", "arguments": {"x": 10}},\n'
        '  {"missing_name": true, "arguments": {}}\n'
        ']\n'
        '```'
    )
    clean, blocks = AnthropicAdapter.parse_tool_calls(raw_invalid_item)
    assert len(blocks) == 0

    raw_all_valid = (
        '```tool_call\n'
        '[\n'
        '  {"name": "valid_tool_1", "arguments": {"x": 10}},\n'
        '  {"name": "valid_tool_2", "arguments": 42}\n'
        ']\n'
        '```'
    )
    clean, blocks = AnthropicAdapter.parse_tool_calls(raw_all_valid)
    assert len(blocks) == 2
    assert blocks[0].name == "valid_tool_1"
    assert blocks[0].input == {"x": 10}
    assert blocks[1].name == "valid_tool_2"
    assert blocks[1].input == {"value": 42}


def test_openai_tool_call_extreme_primitives_safety() -> None:
    """Verifies OpenAIAdapter.parse_tool_calls handles non-dict primitive arguments."""
    raw = (
        'Here is the result:\n'
        '```tool_call\n{"name": "scalar_fn", "arguments": 999}\n```\n'
        '```tool_call\n{"name": "list_fn", "arguments": [1, 2, "a"]}\n```\n'
        '```tool_call\n{"name": "null_fn", "arguments": null}\n```\n'
        'Done.'
    )
    clean, calls = OpenAIAdapter.parse_tool_calls(raw)
    assert len(calls) == 3
    assert calls[0]["function"]["name"] == "scalar_fn"
    assert calls[0]["function"]["arguments"] == "999"
    assert calls[1]["function"]["name"] == "list_fn"
    assert calls[1]["function"]["arguments"] == '[1,2,"a"]'
    assert calls[2]["function"]["name"] == "null_fn"
    assert calls[2]["function"]["arguments"] == "null"
    assert "Here is the result:" in clean
    assert "Done." in clean


# ============================================================================
# Section 2: Unpaired UTF-16 Surrogates in SSE Streams
# ============================================================================

@pytest.mark.asyncio
async def test_openai_sse_unpaired_surrogates_stress_matrix() -> None:
    """Adversarial stress test for OpenAI SSE streaming with diverse unpaired surrogate permutations."""
    surrogate_permutations = [
        "Lone high \ud800 start",
        "Lone high \ud83d middle",
        "Lone high end \udbff",
        "Lone low \udc00 start",
        "Lone low \ude00 middle",
        "Lone low end \udfff",
        "Inverted pair \ude00\ud83d test",
        "Consecutive high \ud83d\ud83d test",
        "Consecutive low \ude00\ude00 test",
        "Mixed text \ud83d valid emoji 😀 and \udc00 tail",
        "Surrogate with null \x00\ud83d\x00 test",
    ]

    for pattern in surrogate_permutations:
        async def gen() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(type="text", delta=pattern)
            yield StreamChunk(type="done", metadata={})

        lines: List[str] = []
        async for line in stream_openai_sse(gen(), "req_surrogate", "gpt-4o", 123456789):
            lines.append(line)
            encoded = line.encode("utf-8")
            assert len(encoded) > 0

            for subline in line.splitlines():
                if subline.startswith("data: "):
                    payload_str = subline[6:].strip()
                    if payload_str != "[DONE]":
                        payload = json.loads(payload_str)
                        assert "choices" in payload
                        assert payload["id"] == "req_surrogate"


@pytest.mark.asyncio
async def test_anthropic_sse_unpaired_surrogates_stress_matrix() -> None:
    """Adversarial stress test for Anthropic SSE streaming with diverse unpaired surrogate permutations."""
    surrogate_permutations = [
        "Anthropic \ud800 lead",
        "Anthropic \ud83d middle",
        "Anthropic \udbff tail",
        "Anthropic low \udc00 lead",
        "Anthropic low \ude00 middle",
        "Anthropic low \udfff tail",
        "Anthropic inverted \ude00\ud83d",
        "Anthropic consecutive \ud83d\ud83d\ud83d",
        "Anthropic with emoji \ud83d🚀\ude00",
    ]

    for pattern in surrogate_permutations:
        async def gen() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(type="text", delta=pattern)
            yield StreamChunk(type="done", metadata={})

        events: List[str] = []
        async for ev in stream_anthropic_sse(gen(), "msg_surrogate", "claude-3-7-sonnet"):
            events.append(ev)
            encoded = ev.encode("utf-8")
            assert len(encoded) > 0

            for subline in ev.splitlines():
                if subline.startswith("data: "):
                    payload = json.loads(subline[6:])
                    assert "type" in payload


# ============================================================================
# Section 3: Anthropic Thinking Block Interleaving & Block Sequencing
# ============================================================================

@pytest.mark.asyncio
async def test_anthropic_sse_rapid_alternation_thinking_and_text() -> None:
    """Verifies strict sequential block ordering when thought and text blocks alternate multiple times."""
    async def alternating_stream() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(type="thought", delta="Thought step 1.")
        yield StreamChunk(type="text", delta="Text observation 1.")
        yield StreamChunk(type="thought", delta="Thought step 2.")
        yield StreamChunk(type="text", delta="Text observation 2.")
        yield StreamChunk(type="thought", delta="Thought step 3.")
        yield StreamChunk(type="text", delta="Text observation 3.")
        yield StreamChunk(type="done", metadata={})

    events: List[Dict[str, Any]] = []
    async for ev in stream_anthropic_sse(alternating_stream(), "msg_alt_seq", "claude-3-7-sonnet", thinking_enabled=True):
        for line in ev.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    starts = [e for e in events if e.get("type") == "content_block_start"]
    stops = [e for e in events if e.get("type") == "content_block_stop"]

    assert len(starts) == 6
    assert len(stops) == 6

    for i in range(6):
        assert starts[i]["index"] == i
        assert stops[i]["index"] == i
        expected_type = "thinking" if i % 2 == 0 else "text"
        assert starts[i]["content_block"]["type"] == expected_type

    assert events[0]["type"] == "message_start"
    assert events[-1]["type"] == "message_stop"
    assert events[-2]["type"] == "message_delta"
    assert events[-2]["delta"]["stop_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_anthropic_sse_thinking_with_tools_enabled() -> None:
    """Verifies thinking blocks stream in real-time, text is buffered, and tool_use blocks append with correct indices."""
    async def tool_stream() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(type="thought", delta="Analyzing tool requirements.")
        yield StreamChunk(type="thought", delta=" Deciding to call calculator.")
        raw_tool_text = (
            'I will calculate the sum.\n'
            '```tool_call\n{"name": "add_numbers", "arguments": {"a": 10, "b": 20}}\n```'
        )
        yield StreamChunk(type="text", delta=raw_tool_text)
        yield StreamChunk(type="done", metadata={})

    events: List[Dict[str, Any]] = []
    async for ev in stream_anthropic_sse(
        tool_stream(),
        "msg_tool_seq",
        "claude-3-7-sonnet",
        thinking_enabled=True,
        has_tools=True,
    ):
        for line in ev.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    starts = [e for e in events if e.get("type") == "content_block_start"]
    stops = [e for e in events if e.get("type") == "content_block_stop"]

    # Block 0: thinking
    assert starts[0]["index"] == 0
    assert starts[0]["content_block"]["type"] == "thinking"
    assert stops[0]["index"] == 0

    # Block 1: clean text
    assert starts[1]["index"] == 1
    assert starts[1]["content_block"]["type"] == "text"
    assert stops[1]["index"] == 1

    # Block 2: tool_use
    assert starts[2]["index"] == 2
    assert starts[2]["content_block"]["type"] == "tool_use"
    assert starts[2]["content_block"]["name"] == "add_numbers"
    assert stops[2]["index"] == 2

    msg_delta = [e for e in events if e.get("type") == "message_delta"][0]
    assert msg_delta["delta"]["stop_reason"] == "tool_use"


@pytest.mark.asyncio
async def test_anthropic_sse_thinking_disabled_skips_thought_chunks() -> None:
    """Verifies thinking chunks are ignored when thinking_enabled is False."""
    async def thought_stream() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(type="thought", delta="Hidden thought.")
        yield StreamChunk(type="text", delta="Visible text.")
        yield StreamChunk(type="done", metadata={})

    events: List[Dict[str, Any]] = []
    async for ev in stream_anthropic_sse(thought_stream(), "msg_no_think", "claude-3-7-sonnet", thinking_enabled=False):
        for line in ev.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    starts = [e for e in events if e.get("type") == "content_block_start"]
    assert len(starts) == 1
    assert starts[0]["content_block"]["type"] == "text"


@pytest.mark.asyncio
async def test_anthropic_sse_stop_sequence_interruption() -> None:
    """Verifies stop_sequence stops the stream immediately and closes active blocks cleanly."""
    async def text_stream() -> AsyncIterator[StreamChunk]:
        yield StreamChunk(type="thought", delta="Thinking start.")
        yield StreamChunk(type="text", delta="First part before STOP_HERE and more text after.")
        yield StreamChunk(type="text", delta="This chunk should never be processed.")
        yield StreamChunk(type="done", metadata={})

    events: List[Dict[str, Any]] = []
    async for ev in stream_anthropic_sse(
        text_stream(),
        "msg_stop_seq",
        "claude-3-7-sonnet",
        thinking_enabled=True,
        stop_sequences=["STOP_HERE"],
    ):
        for line in ev.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    deltas = [e.get("delta", {}).get("text", "") for e in events if e.get("type") == "content_block_delta"]
    assert not any("never be processed" in d for d in deltas)

    msg_delta = [e for e in events if e.get("type") == "message_delta"][0]
    assert msg_delta["delta"]["stop_reason"] == "stop_sequence"
    assert msg_delta["delta"]["stop_sequence"] == "STOP_HERE"


# ============================================================================
# Section 4: Empty Batch & Malformed JSON-RPC Calls on /mcp
# ============================================================================

@pytest.mark.asyncio
async def test_mcp_batch_adversarial_matrix() -> None:
    """Verifies MCPServer handles batches of invalid primitives and mixed requests per JSON-RPC 2.0."""
    server = MCPServer()

    # 1. Batch of non-dict primitives: [None, 123, "hello", True, []]
    res_primitives = await server.handle_message([None, 123, "hello", True, []])
    assert isinstance(res_primitives, list)
    assert len(res_primitives) == 5
    for item in res_primitives:
        assert item["jsonrpc"] == "2.0"
        assert item["error"]["code"] == -32600

    # 2. Batch of pure notifications returns None
    res_notif = await server.handle_message([
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ])
    assert res_notif is None

    # 3. Batch mixed with notification and request
    res_mixed = await server.handle_message([
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "method": "ping", "id": "req_ping_1"},
    ])
    assert isinstance(res_mixed, list)
    assert len(res_mixed) == 1
    assert res_mixed[0]["id"] == "req_ping_1"
    assert res_mixed[0]["result"] == {}

    # 4. Top-level scalar non-dict / non-list returns error -32600
    res_scalar = await server.handle_message("string_not_json")  # type: ignore[arg-type]
    assert isinstance(res_scalar, dict)
    assert res_scalar["error"]["code"] == -32600


def test_mcp_malformed_json_syntax_returns_32700() -> None:
    """Verifies sending syntactically malformed JSON body to /mcp returns HTTP 400 with -32700."""
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            content=b'{"unclosed_json": true,',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == -32700
        assert "Parse error" in body["error"]["message"]


@pytest.mark.asyncio
async def test_mcp_tools_call_invalid_params() -> None:
    """Verifies tools/call with invalid params or unknown tool returns proper JSON-RPC error codes."""
    server = MCPServer()

    # 1. Params not a dict -> -32602
    res_not_dict = await server.handle_message({
        "jsonrpc": "2.0",
        "id": "call_bad_params",
        "method": "tools/call",
        "params": "string_not_dict",
    })
    assert isinstance(res_not_dict, dict)
    assert res_not_dict["error"]["code"] == -32602

    # 2. Missing tool name -> -32601
    res_no_name = await server.handle_message({
        "jsonrpc": "2.0",
        "id": "call_no_name",
        "method": "tools/call",
        "params": {"arguments": {}},
    })
    assert isinstance(res_no_name, dict)
    assert res_no_name["error"]["code"] == -32601

    # 3. Unknown tool name -> -32601
    res_unknown = await server.handle_message({
        "jsonrpc": "2.0",
        "id": "call_unknown",
        "method": "tools/call",
        "params": {"name": "non_existent_tool_xyz", "arguments": {}},
    })
    assert isinstance(res_unknown, dict)
    assert res_unknown["error"]["code"] == -32601
    assert "Tool 'non_existent_tool_xyz' not found" in res_unknown["error"]["message"]


# ============================================================================
# Section 5: High Concurrency SQLite WAL Writes in ConversationCache
# ============================================================================

def test_conversation_cache_multi_instance_concurrent_wal_stress(tmp_path: Path) -> None:
    """
    Stress-tests multi-instance concurrency against a single SQLite WAL database file.
    Simulates 4 independent worker processes with 24 concurrent threads performing
    rapid interleaved writes, queries, and LRU pruning.
    """
    db_file = tmp_path / "shared_multi_instance_wal.db"

    cache_instances = [
        ConversationCache(max_size=100, db_path=db_file)
        for _ in range(4)
    ]

    errors: List[Exception] = []
    thread_count = 24
    writes_per_thread = 25

    def stress_worker(worker_id: int) -> None:
        try:
            cache = cache_instances[worker_id % len(cache_instances)]
            for i in range(writes_per_thread):
                msgs = [
                    {"role": "user", "content": f"Worker {worker_id} Prompt {i}"},
                ]
                reply = {"role": "assistant", "content": f"Worker {worker_id} Reply {i}"}
                chat = {
                    "cid": f"cid_{worker_id}_{i}",
                    "rid": f"rid_{worker_id}_{i}",
                    "rcid": f"rcid_{worker_id}_{i}",
                }
                key = cache.remember(msgs, reply, chat, model="argon", account="acc_main")
                assert key is not None

                other_cache = cache_instances[(worker_id + 1) % len(cache_instances)]
                matched, found = other_cache.find(msgs + [reply, {"role": "user", "content": "next"}], model="argon", account="acc_main")
                if found:
                    assert found["cid"].startswith("cid_")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=stress_worker, args=(t,)) for t in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Encountered errors during concurrent WAL stress: {errors}"

    with sqlite3.connect(str(db_file)) as conn:
        integrity_row = conn.execute("PRAGMA integrity_check;").fetchone()
        assert integrity_row[0] == "ok"


# ============================================================================
# Section 6: Conversation Cache Prefix Matching & Collision Immunity
# ============================================================================

def test_conversation_cache_multi_turn_longest_prefix_matching(tmp_path: Path) -> None:
    """Verifies cache.find() accurately returns the LONGEST matching prefix in multi-turn dialogues."""
    db_file = tmp_path / "prefix_match.db"
    cache = ConversationCache(max_size=100, db_path=db_file)

    t1_user = {"role": "user", "content": "Turn 1 question"}
    t1_reply = {"role": "assistant", "content": "Turn 1 answer"}
    chat_state_1 = {"cid": "cid_t1", "rid": "rid_t1", "rcid": "rcid_t1"}

    t2_user = {"role": "user", "content": "Turn 2 question"}
    t2_reply = {"role": "assistant", "content": "Turn 2 answer"}
    chat_state_2 = {"cid": "cid_t2", "rid": "rid_t2", "rcid": "rcid_t2"}

    cache.remember([t1_user], t1_reply, chat_state_1)
    cache.remember([t1_user, t1_reply, t2_user], t2_reply, chat_state_2)

    full_turn_3 = [t1_user, t1_reply, t2_user, t2_reply, {"role": "user", "content": "Turn 3 question"}]
    matched_len, state = cache.find(full_turn_3)
    assert matched_len == 4
    assert state is not None
    assert state["cid"] == "cid_t2"


def test_conversation_cache_subtle_content_collision_immunity(tmp_path: Path) -> None:
    """Verifies minor content or tool call variances generate distinct hash keys with zero collisions."""
    base_msg = [{"role": "user", "content": "Test prompt"}]

    # 1. Trailing whitespace difference
    msg_trailing_space = [{"role": "user", "content": "Test prompt "}]
    assert history_key(base_msg) != history_key(msg_trailing_space)

    # 2. Case difference
    msg_case = [{"role": "user", "content": "test prompt"}]
    assert history_key(base_msg) != history_key(msg_case)

    # 3. None vs empty string
    msg_none = [{"role": "user", "content": None}]
    msg_empty = [{"role": "user", "content": ""}]
    assert history_key(msg_none) == history_key(msg_empty)

    # 4. Tool calls distinction
    msg_with_tool = [
        {"role": "user", "content": "Test prompt"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": "calculator", "arguments": '{"a": 1}'},
                }
            ],
        },
    ]
    msg_with_different_tool = [
        {"role": "user", "content": "Test prompt"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {"name": "calculator", "arguments": '{"a": 2}'},
                }
            ],
        },
    ]
    assert history_key(msg_with_tool) != history_key(msg_with_different_tool)


# ============================================================================
# Section 7: Subagent Auto-Routing & MCP Extended Protocol Compliance
# ============================================================================

def test_subagent_profile_detection_adversarial_inputs() -> None:
    """Verifies detect_subagent_profile handles None, empty, or embedded inputs without error or false positives."""
    # 1. Empty or None
    assert detect_subagent_profile("") is None
    assert detect_subagent_profile("   ") is None
    assert detect_subagent_profile(None) is None  # type: ignore[arg-type]

    # 2. Prevent false positives on unrelated prompts mentioning keywords
    assert detect_subagent_profile("Please scout the codebase for any explorer patterns.") is None
    assert detect_subagent_profile("I am an architect building a house.") is None

    # 3. Valid agent prompt embedded inside larger system instructions matches
    sys_instruction = (
        "Environment: Windows 11\n\n"
        "You are the **Codebase Explorer Scout** for `gemini_local_ui`.\n"
        "When invoked:\n"
        "1. Inspect files.\n"
    )
    scout_prof = detect_subagent_profile(sys_instruction)
    assert scout_prof is not None
    assert scout_prof["subagent"] == "explorer-scout"
    assert scout_prof["model"] == "56fdd199312815e2"

    # 4. Argon architect detection embedded
    argon_prompt = (
        "System Header\n\n"
        "You are the **Argon Chief Architect** for `gemini_local_ui`.\n"
    )
    argon_prof = detect_subagent_profile(argon_prompt)
    assert argon_prof is not None
    assert argon_prof["subagent"] == "argon-architect"
    assert argon_prof["model"] == "95a4c0abcba24364"
    assert argon_prof["feature_mode"] == 40


@pytest.mark.asyncio
async def test_mcp_extended_method_dispatch() -> None:
    """Verifies unknown methods return -32601 and standard protocol methods return compliant envelopes."""
    server = MCPServer()

    # 1. Unknown method returns -32601
    res_unknown_method = await server.handle_message({
        "jsonrpc": "2.0",
        "id": "unknown_1",
        "method": "custom/non_existent_method",
        "params": {},
    })
    assert isinstance(res_unknown_method, dict)
    assert res_unknown_method["error"]["code"] == -32601

    # 2. initialize method returns protocolVersion and capabilities
    res_init = await server.handle_message({
        "jsonrpc": "2.0",
        "id": "init_1",
        "method": "initialize",
        "params": {},
    })
    assert isinstance(res_init, dict)
    assert res_init["result"]["protocolVersion"] == "2024-11-05"
    assert "capabilities" in res_init["result"]
    assert "serverInfo" in res_init["result"]

    # 3. tools/list returns non-empty list of tools with inputSchema
    res_tools = await server.handle_message({
        "jsonrpc": "2.0",
        "id": "tools_1",
        "method": "tools/list",
        "params": {},
    })
    assert isinstance(res_tools, dict)
    tools = res_tools["result"]["tools"]
    assert len(tools) >= 5
    tool_names = {t["name"] for t in tools}
    assert "gemini_argon_chat" in tool_names
    assert "gemini_argon_task" in tool_names
    assert "notebooklm_list" in tool_names
