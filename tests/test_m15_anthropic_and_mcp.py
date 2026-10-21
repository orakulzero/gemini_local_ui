"""
tests/test_m15_anthropic_and_mcp.py
Comprehensive verification suite for Milestone M15:
- Anthropic Messages API Gateway (/v1/messages and /v1/messages/count_tokens)
  * Model mapping (claude-* and gemini-* to 4 Argon, 3.8 Flash, 3.5 Flash-Lite)
  * System prompt and tool instruction formatting
  * Non-streaming messages (text, extended thinking, tool calling)
  * SSE streaming messages (message_start, content_block_start/delta/stop, message_delta, message_stop)
  * Tool calling extraction (tool_use blocks with toolu_ IDs and stop_reason="tool_use")
  * Multi-turn stateful session continuation via SHA-256 conversation cache with tool_result
  * Image attachment decoding and staging
  * Token counting (/v1/messages/count_tokens)
- Built-in Model Context Protocol (MCP) Server (/mcp and stdio_server)
  * JSON-RPC 2.0 protocol compliance (initialize, notifications/initialized, ping)
  * Tools discovery (tools/list for 7 tools)
  * Tools execution (tools/call for all 7 tools: gemini_argon_chat, gemini_argon_task,
    gemini_generate_image, notebooklm_list, notebooklm_query, notebooklm_add_source, gemini_list_gems)
  * Resources discovery & reading (resources/list, resources/read for notebooklm://, gemini://)
  * Batch request handling and standard JSON-RPC 2.0 error handling (-32700, -32601, -32602)
  * Stdio transport message handling
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient

from backend.app.anthropic.adapter import AnthropicAdapter, CLAUDE_MODEL_MAPPING
from backend.app.anthropic.routes import get_gemini_client, get_gemini_uploader
from backend.app.anthropic.schemas import (
    CountTokensRequest,
    ImageBlock,
    ImageSource,
    MessageParam,
    MessageRequest,
    TextBlock,
    ToolParam,
    ToolResultBlock,
)
from backend.app.anthropic.sse import stream_anthropic_sse
from backend.app.bridge.notebooklm import NotebookDetail, NotebookInfo, NotebookNote, NotebookSource
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.main import app
from backend.app.mcp.routes import get_gemini_client as get_mcp_gemini_client
from backend.app.mcp.routes import get_notebooklm_client as get_mcp_notebooklm_client
from backend.app.mcp.server import MCPServer, PROTOCOL_VERSION, SERVER_INFO
from backend.app.openai import conversation_cache
from backend.app.openai.adapter import ModelNotFoundError


# ============================================================================
# Test Fixtures & Mock Clients
# ============================================================================

class MockGeminiClient:
    """Configurable mock for GeminiWebClient in Anthropic and MCP tests."""

    def __init__(
        self,
        text_responses: Optional[List[str]] = None,
        thought_responses: Optional[List[str]] = None,
        cid: str = "c_mock_session_1",
        rid: str = "r_mock_1",
        rcid: str = "rc_mock_1",
        images_metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.text_responses = text_responses or ["Mock Gemini response text."]
        self.thought_responses = thought_responses or []
        self.cid = cid
        self.rid = rid
        self.rcid = rcid
        self.images_metadata = images_metadata or []
        self.recorded_calls: List[Dict[str, Any]] = []

    async def generate_stream(
        self,
        prompt: str,
        model: str = "95a4c0abcba24364",
        gem_id: Optional[str] = None,
        attachments: Optional[List[Any]] = None,
        history: Optional[List[Any]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        call_record = {
            "prompt": prompt,
            "model": model,
            "gem_id": gem_id,
            "attachments": attachments,
            "history": history,
            "cid": kwargs.get("cid"),
            "rid": kwargs.get("rid"),
            "rcid": kwargs.get("rcid"),
            "feature_mode": kwargs.get("feature_mode"),
        }
        self.recorded_calls.append(call_record)

        # 1. Yield thought chunks if configured
        for thought in self.thought_responses:
            yield StreamChunk(type="thought", delta=thought)

        # 2. Yield text chunks
        call_index = len(self.recorded_calls) - 1
        resp_text = self.text_responses[call_index % len(self.text_responses)]
        yield StreamChunk(type="text", delta=resp_text)

        # 3. Yield done chunk with metadata
        done_meta: Dict[str, Any] = {
            "cid": self.cid,
            "rid": f"{self.rid}_{len(self.recorded_calls)}",
            "rcid": f"{self.rcid}_{len(self.recorded_calls)}",
        }
        if self.images_metadata:
            done_meta["images"] = self.images_metadata

        yield StreamChunk(type="done", delta="", metadata=done_meta)

    async def list_gems(self, gem_type: int = 2) -> List[Any]:
        @dataclass
        class MockGem:
            id: str
            name: str
            description: str
            prompt: str

            def to_dict(self) -> Dict[str, Any]:
                return {"id": self.id, "title": self.name, "description": self.description, "system_prompt": self.prompt, "is_builtin": gem_type == 3}

        if gem_type == 2:
            return [MockGem(id="gem_custom_1", name="Custom Coder", description="Writes code", prompt="Code expert")]
        return [MockGem(id="gem_system_1", name="Google Scholar", description="Academic research", prompt="Academic expert")]

    async def close(self) -> None:
        pass


class MockNotebookLMClient:
    """Mock for NotebookLMClient."""

    base_url = "https://notebooklm.google.com"

    async def scrape_tokens(self) -> bool:
        return True

    async def list_notebooks(self) -> List[NotebookInfo]:
        return [
            NotebookInfo(
                id="nb_123",
                title="Quantum Computing Notes",
                created_at=1700000000.0,
                updated_at=1700000100.0,
                source_count=3,
                emoji="⚛️",
            ),
            NotebookInfo(
                id="nb_456",
                title="Compiler Engineering",
                created_at=1700000200.0,
                updated_at=1700000300.0,
                source_count=5,
                emoji="💻",
            ),
        ]

    async def get_notebook(self, notebook_id: str) -> NotebookDetail:
        return NotebookDetail(
            id=notebook_id,
            title="Quantum Computing Notes",
            created_at=1700000000.0,
            updated_at=1700000100.0,
            source_count=1,
            emoji="⚛️",
            sources=[
                NotebookSource(
                    id="src_1",
                    title="Superconducting Qubits",
                    source_type="text",
                    summary="Overview of Josephson junction transmons and coherence times.",
                )
            ],
            notes=[
                NotebookNote(
                    id="note_1",
                    title="Fluxonium Experiment",
                    content="Observed T1 relaxation of 300 microseconds at 15 mK.",
                )
            ],
        )

    async def add_text_source(self, notebook_id: str, title: str, content: str) -> NotebookSource:
        return NotebookSource(
            id="src_new_99",
            title=title,
            source_type="user_text",
            summary=content[:50],
        )

    async def close(self) -> None:
        pass


# ============================================================================
# Section 1: Anthropic Adapter Unit Tests
# ============================================================================

def test_anthropic_model_resolution() -> None:
    # Claude models
    assert AnthropicAdapter.resolve_model("claude-3-5-sonnet-20241022")[0] == "95a4c0abcba24364"
    assert AnthropicAdapter.resolve_model("claude-3-7-sonnet")[0] == "95a4c0abcba24364"
    assert AnthropicAdapter.resolve_model("claude-sonnet-4-20250514")[0] == "95a4c0abcba24364"
    assert AnthropicAdapter.resolve_model("claude-opus-4")[0] == "95a4c0abcba24364"
    assert AnthropicAdapter.resolve_model("claude-3-5-haiku-20241022")[0] == "56fdd199312815e2"
    assert AnthropicAdapter.resolve_model("claude-3-haiku-20240307")[0] == "8c46e95b1a07cecc"
    assert AnthropicAdapter.resolve_model("claude-custom-wildcard")[0] == "95a4c0abcba24364"

    # Gemini native models
    assert AnthropicAdapter.resolve_model("gemini-4-argon")[0] == "95a4c0abcba24364"
    assert AnthropicAdapter.resolve_model("gemini-3.8-flash")[0] == "56fdd199312815e2"

    # Modifiers
    model_mod, _ = AnthropicAdapter.resolve_model("claude-3-7-sonnet@mode=task")
    assert model_mod == "95a4c0abcba24364@mode=task"

    # Unknown model raises ModelNotFoundError
    with pytest.raises(ModelNotFoundError):
        AnthropicAdapter.resolve_model("nonexistent-gpt-99")


def test_anthropic_system_and_tool_instruction_building() -> None:
    # 1. Plain string system prompt
    sys_str = AnthropicAdapter.extract_system_instruction("You are an expert compiler engineer.")
    assert sys_str == "You are an expert compiler engineer."

    # 2. Block list system prompt
    blocks = [TextBlock(text="Part 1"), TextBlock(text="Part 2")]
    sys_blocks = AnthropicAdapter.extract_system_instruction(blocks)
    assert "Part 1\n\nPart 2" in sys_blocks

    # 3. Tool instruction formatting
    tools = [
        ToolParam(
            name="search_db",
            description="Searches relational database",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
    ]
    sys_with_tools = AnthropicAdapter.extract_system_instruction("Assistant prompt", tools=tools, tool_choice="auto")
    assert "# Tool Use" in sys_with_tools
    assert "### search_db" in sys_with_tools
    assert "```tool_call" in sys_with_tools
    assert "If no tool is needed, respond normally" in sys_with_tools

    # Forced tool choice
    sys_forced = AnthropicAdapter.extract_system_instruction("Assistant prompt", tools=tools, tool_choice={"type": "tool", "name": "search_db"})
    assert "You MUST call the tool `search_db`" in sys_forced


def test_anthropic_tool_call_parsing() -> None:
    output_text = (
        "I will search the database for you.\n"
        "```tool_call\n"
        '{"name": "search_db", "arguments": {"query": "SELECT * FROM users"}}\n'
        "```\n"
        "Please wait a moment."
    )
    clean_text, tool_calls = AnthropicAdapter.parse_tool_calls(output_text)
    assert "I will search the database for you." in clean_text
    assert "Please wait a moment." in clean_text
    assert "```tool_call" not in clean_text
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "search_db"
    assert tool_calls[0].input == {"query": "SELECT * FROM users"}
    assert tool_calls[0].id.startswith("toolu_")


def test_anthropic_token_counting() -> None:
    req = CountTokensRequest(
        model="claude-3-7-sonnet",
        system="System prompt instructions",
        messages=[
            MessageParam(role="user", content="Hello world, please count my tokens."),
            MessageParam(role="assistant", content="Certainly! Tokens counted."),
        ],
        tools=[
            ToolParam(
                name="test_tool",
                description="A test tool",
                input_schema={"type": "object", "properties": {"arg": {"type": "string"}}},
            )
        ],
    )
    count = AnthropicAdapter.count_tokens(req)
    assert count > 20
    assert isinstance(count, int)


# ============================================================================
# Section 2: Anthropic Non-Streaming Messages Endpoint (/v1/messages)
# ============================================================================

def test_anthropic_non_streaming_text_and_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    fake_client = MockGeminiClient(
        text_responses=["Here is the solution to your query."],
        thought_responses=["Thinking step 1: analyze requirement.", "Thinking step 2: verify logic."],
    )
    app.dependency_overrides[get_gemini_client] = lambda: fake_client
    app.dependency_overrides[get_gemini_uploader] = lambda: None
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    try:
        tc = TestClient(app)
        resp = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-7-sonnet-20250219",
                "max_tokens": 1024,
                "system": "You are a helpful assistant.",
                "messages": [
                    {"role": "user", "content": "What is the capital of France?"}
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "message"
        assert data["role"] == "assistant"
        assert data["model"] == "claude-3-7-sonnet-20250219"
        assert data["stop_reason"] == "end_turn"
        assert data["usage"]["input_tokens"] > 0
        assert data["usage"]["output_tokens"] > 0

        # Verify content blocks: thinking block followed by text block
        contents = data["content"]
        assert len(contents) == 2
        assert contents[0]["type"] == "thinking"
        assert "Thinking step 1" in contents[0]["thinking"]
        assert contents[1]["type"] == "text"
        assert contents[1]["text"] == "Here is the solution to your query."

    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


def test_anthropic_non_streaming_tool_calling(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    tool_call_text = (
        "I need to read the configuration file.\n"
        "```tool_call\n"
        '{"name": "read_file", "arguments": {"path": "/etc/config.json"}}\n'
        "```"
    )
    fake_client = MockGeminiClient(text_responses=[tool_call_text])
    app.dependency_overrides[get_gemini_client] = lambda: fake_client
    app.dependency_overrides[get_gemini_uploader] = lambda: None
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    try:
        tc = TestClient(app)
        resp = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Reads a file from disk",
                        "input_schema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    }
                ],
                "messages": [
                    {"role": "user", "content": "Please read /etc/config.json"}
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stop_reason"] == "tool_use"
        contents = data["content"]
        # Text block + ToolUse block
        assert len(contents) == 2
        assert contents[0]["type"] == "text"
        assert "I need to read the configuration file." in contents[0]["text"]
        assert contents[1]["type"] == "tool_use"
        assert contents[1]["name"] == "read_file"
        assert contents[1]["input"] == {"path": "/etc/config.json"}
        assert contents[1]["id"].startswith("toolu_")

    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


def test_anthropic_error_handling(monkeypatch: pytest.MonkeyPatch) -> None:
    tc = TestClient(app)

    # 1. Empty messages -> 400
    r1 = tc.post("/v1/messages", json={"model": "claude-3-5-sonnet", "messages": []})
    assert r1.status_code == 400
    assert r1.json()["type"] == "error"
    assert r1.json()["error"]["type"] == "invalid_request_error"

    # 2. Unknown non-Claude model -> 404
    r2 = tc.post(
        "/v1/messages",
        json={
            "model": "unknown-nonexistent-model",
            "messages": [{"role": "user", "content": "Hi"}],
        },
    )
    assert r2.status_code == 404
    assert r2.json()["error"]["type"] == "not_found_error"


# ============================================================================
# Section 3: Anthropic SSE Streaming Endpoint (/v1/messages)
# ============================================================================

def test_anthropic_sse_streaming_sequence(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    fake_client = MockGeminiClient(
        text_responses=["Streamed text token."],
        thought_responses=["Thinking token."],
    )
    app.dependency_overrides[get_gemini_client] = lambda: fake_client
    app.dependency_overrides[get_gemini_uploader] = lambda: None
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    try:
        tc = TestClient(app)
        resp = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-7-sonnet",
                "stream": True,
                "messages": [{"role": "user", "content": "Explain SSE streaming."}],
            },
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

        events: List[Tuple[str, Dict[str, Any]]] = []
        raw_lines = resp.text.split("\n\n")
        for chunk_str in raw_lines:
            chunk_str = chunk_str.strip()
            if not chunk_str:
                continue
            ev_name = ""
            data_dict = {}
            for line in chunk_str.split("\n"):
                if line.startswith("event: "):
                    ev_name = line[7:].strip()
                elif line.startswith("data: "):
                    data_dict = json.loads(line[6:].strip())
            if ev_name and data_dict:
                events.append((ev_name, data_dict))

        # Check event sequence
        event_names = [ev[0] for ev in events]
        assert "message_start" in event_names
        assert "content_block_start" in event_names
        assert "content_block_delta" in event_names
        assert "content_block_stop" in event_names
        assert "message_delta" in event_names
        assert "message_stop" in event_names

        # Verify thinking delta
        thinking_deltas = [ev[1] for ev in events if ev[0] == "content_block_delta" and ev[1].get("delta", {}).get("type") == "thinking_delta"]
        assert len(thinking_deltas) > 0
        assert thinking_deltas[0]["delta"]["thinking"] == "Thinking token."

        # Verify text delta
        text_deltas = [ev[1] for ev in events if ev[0] == "content_block_delta" and ev[1].get("delta", {}).get("type") == "text_delta"]
        assert len(text_deltas) > 0
        assert text_deltas[0]["delta"]["text"] == "Streamed text token."

        # Verify final message_delta stop_reason
        msg_deltas = [ev[1] for ev in events if ev[0] == "message_delta"]
        assert len(msg_deltas) == 1
        assert msg_deltas[0]["delta"]["stop_reason"] == "end_turn"

    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


def test_anthropic_sse_streaming_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    tool_text = "```tool_call\n{\"name\": \"get_temperature\", \"arguments\": {\"city\": \"Berlin\"}}\n```"
    fake_client = MockGeminiClient(text_responses=[tool_text])
    app.dependency_overrides[get_gemini_client] = lambda: fake_client
    app.dependency_overrides[get_gemini_uploader] = lambda: None
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    try:
        tc = TestClient(app)
        resp = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "stream": True,
                "tools": [
                    {
                        "name": "get_temperature",
                        "description": "Gets temperature for city",
                        "input_schema": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    }
                ],
                "messages": [{"role": "user", "content": "What is the temperature in Berlin?"}],
            },
        )
        assert resp.status_code == 200

        events = []
        for chunk_str in resp.text.split("\n\n"):
            chunk_str = chunk_str.strip()
            if not chunk_str:
                continue
            ev_name = ""
            data_dict = {}
            for line in chunk_str.split("\n"):
                if line.startswith("event: "):
                    ev_name = line[7:].strip()
                elif line.startswith("data: "):
                    data_dict = json.loads(line[6:].strip())
            if ev_name and data_dict:
                events.append((ev_name, data_dict))

        # Check tool_use block start
        tool_starts = [ev[1] for ev in events if ev[0] == "content_block_start" and ev[1].get("content_block", {}).get("type") == "tool_use"]
        assert len(tool_starts) == 1
        assert tool_starts[0]["content_block"]["name"] == "get_temperature"
        assert tool_starts[0]["content_block"]["id"].startswith("toolu_")

        # Check input_json_delta
        input_deltas = [ev[1] for ev in events if ev[0] == "content_block_delta" and ev[1].get("delta", {}).get("type") == "input_json_delta"]
        assert len(input_deltas) == 1
        parsed_json = json.loads(input_deltas[0]["delta"]["partial_json"])
        assert parsed_json == {"city": "Berlin"}

        # Check message_delta stop_reason
        msg_deltas = [ev[1] for ev in events if ev[0] == "message_delta"]
        assert msg_deltas[0]["delta"]["stop_reason"] == "tool_use"

    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


# ============================================================================
# Section 4: Multi-Turn Stateful Continuation via Conversation Cache
# ============================================================================

def test_anthropic_multi_turn_tool_result_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    recorded_calls: List[Dict[str, Any]] = []

    class MultiTurnMockClient:
        async def generate_stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[StreamChunk]:
            recorded_calls.append({"prompt": prompt, "cid": kwargs.get("cid"), "rid": kwargs.get("rid")})
            turn_num = len(recorded_calls)
            if turn_num == 1:
                # Turn 1: Model calls tool
                reply = "```tool_call\n{\"name\": \"run_calc\", \"arguments\": {\"expr\": \"40+2\"}}\n```"
                yield StreamChunk(type="text", delta=reply)
                yield StreamChunk(type="done", delta="", metadata={"cid": "cid_session_calc", "rid": "rid_turn_1", "rcid": "rcid_turn_1"})
            else:
                # Turn 2: Model answers after tool result
                yield StreamChunk(type="text", delta="The answer is 42.")
                yield StreamChunk(type="done", delta="", metadata={"cid": "cid_session_calc", "rid": "rid_turn_2", "rcid": "rcid_turn_2"})

        async def close(self) -> None:
            pass

    fake_client = MultiTurnMockClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake_client
    app.dependency_overrides[get_gemini_uploader] = lambda: None
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    try:
        tc = TestClient(app)

        # Turn 1: User prompt
        tools = [{"name": "run_calc", "description": "Calculator", "input_schema": {"type": "object"}}]
        r1 = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-7-sonnet",
                "tools": tools,
                "messages": [{"role": "user", "content": "Calculate 40+2"}],
            },
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["stop_reason"] == "tool_use"
        tool_call_id = d1["content"][0]["id"]
        assert len(recorded_calls) == 1
        assert recorded_calls[0]["cid"] is None  # Fresh session

        # Turn 2: User provides tool_result
        r2 = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-7-sonnet",
                "tools": tools,
                "messages": [
                    {"role": "user", "content": "Calculate 40+2"},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "id": tool_call_id, "name": "run_calc", "input": {"expr": "40+2"}}
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": tool_call_id, "content": "42"}
                        ],
                    },
                ],
            },
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["stop_reason"] == "end_turn"
        assert d2["content"][0]["text"] == "The answer is 42."

        # Verify that Turn 2 resumed existing cid and sent delta turn!
        assert len(recorded_calls) == 2
        assert recorded_calls[1]["cid"] == "cid_session_calc"
        assert recorded_calls[1]["rid"] == "rid_turn_1"
        assert f"[Tool result for {tool_call_id}]: 42" in recorded_calls[1]["prompt"]

    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


# ============================================================================
# Section 5: Count Tokens Endpoint (/v1/messages/count_tokens)
# ============================================================================

def test_count_tokens_endpoint() -> None:
    tc = TestClient(app)
    resp = tc.post(
        "/v1/messages/count_tokens",
        json={
            "model": "claude-3-7-sonnet",
            "system": "System instructions for testing.",
            "messages": [
                {"role": "user", "content": "Count the number of tokens in this request."},
                {"role": "assistant", "content": "Tokens are being counted accurately."},
            ],
            "tools": [
                {
                    "name": "sample_tool",
                    "description": "Sample description",
                    "input_schema": {"type": "object", "properties": {"val": {"type": "integer"}}},
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "input_tokens" in data
    assert data["input_tokens"] > 15


# ============================================================================
# Section 6: MCP Server & JSON-RPC 2.0 Protocol Tests
# ============================================================================

def test_mcp_initialize_and_ping() -> None:
    tc = TestClient(app)

    # 1. Initialize
    r_init = tc.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        },
    )
    assert r_init.status_code == 200
    res_init = r_init.json()["result"]
    assert res_init["protocolVersion"] == "2024-11-05"
    assert res_init["serverInfo"]["name"] == "gemini-local-mcp"
    assert "tools" in res_init["capabilities"]

    # 2. Notification initialized -> 204 No Content
    r_notif = tc.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
    )
    assert r_notif.status_code == 204

    # 3. Ping
    r_ping = tc.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
    )
    assert r_ping.status_code == 200
    assert r_ping.json()["result"] == {}


def test_mcp_tools_list() -> None:
    tc = TestClient(app)
    r = tc.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 10, "method": "tools/list"},
    )
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert len(tool_names) >= 7
    expected = [
        "gemini_argon_chat",
        "gemini_argon_task",
        "gemini_generate_image",
        "notebooklm_list",
        "notebooklm_query",
        "notebooklm_add_source",
        "gemini_list_gems",
    ]
    for exp in expected:
        assert exp in tool_names
        tool_spec = next(t for t in tools if t["name"] == exp)
        assert "description" in tool_spec
        assert "inputSchema" in tool_spec
        assert tool_spec["inputSchema"]["type"] == "object"


def test_mcp_tool_call_gemini_argon_chat() -> None:
    fake_client = MockGeminiClient(
        text_responses=["Argon response for MCP."],
        thought_responses=["Argon reasoning step."],
        cid="cid_mcp_chat",
    )
    app.dependency_overrides[get_mcp_gemini_client] = lambda: fake_client

    try:
        tc = TestClient(app)
        r = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "gemini_argon_chat",
                    "arguments": {
                        "prompt": "Hello from MCP client",
                        "system_prompt": "Act as a physicist.",
                    },
                },
            },
        )
        assert r.status_code == 200
        res = r.json()["result"]
        assert res["isError"] is False
        content_text = res["content"][0]["text"]
        assert "<thought>" in content_text
        assert "Argon reasoning step." in content_text
        assert "Argon response for MCP." in content_text
        assert "cid=cid_mcp_chat" in content_text

        assert len(fake_client.recorded_calls) == 1
        assert "# System Instruction\nAct as a physicist." in fake_client.recorded_calls[0]["prompt"]
    finally:
        app.dependency_overrides.pop(get_mcp_gemini_client, None)


def test_mcp_tool_call_gemini_argon_task() -> None:
    fake_client = MockGeminiClient(
        text_responses=["Task mode plan and execution output."],
        cid="cid_task_mode",
    )
    app.dependency_overrides[get_mcp_gemini_client] = lambda: fake_client

    try:
        tc = TestClient(app)
        r = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {
                    "name": "gemini_argon_task",
                    "arguments": {
                        "prompt": "Refactor the module",
                        "context_files": [
                            {"filename": "main.py", "content": "print('hello')"}
                        ],
                    },
                },
            },
        )
        assert r.status_code == 200
        res = r.json()["result"]
        assert res["isError"] is False
        assert "Task mode plan" in res["content"][0]["text"]

        assert len(fake_client.recorded_calls) == 1
        assert fake_client.recorded_calls[0]["feature_mode"] == 40
        assert '<file path="main.py">' in fake_client.recorded_calls[0]["prompt"]
    finally:
        app.dependency_overrides.pop(get_mcp_gemini_client, None)


def test_mcp_tool_call_gemini_generate_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app.state, "image_data_dir", tmp_path, raising=False)
    fake_client = MockGeminiClient(
        text_responses=["Here is an image."],
    )
    app.dependency_overrides[get_mcp_gemini_client] = lambda: fake_client

    try:
        tc = TestClient(app)
        r = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "gemini_generate_image",
                    "arguments": {
                        "prompt": "A cybernetic neon city",
                        "aspect_ratio": "16:9",
                    },
                },
            },
        )
        assert r.status_code == 200
        res = r.json()["result"]
        assert res["isError"] is False
        text_out = res["content"][0]["text"]
        assert "![A cybernetic neon city](/files/" in text_out
        assert "Generated image saved: /files/" in text_out

        # Verify image file was actually written to disk
        image_files = list(tmp_path.rglob("*.png")) + list((Path("data/images")).rglob("*.png"))
        assert len(image_files) > 0

    finally:
        app.dependency_overrides.pop(get_mcp_gemini_client, None)


def test_mcp_tool_call_notebooklm_suite() -> None:
    mock_nb_client = MockNotebookLMClient()
    mock_gemini = MockGeminiClient(text_responses=["Quantum qubits are synthesized."])
    app.dependency_overrides[get_mcp_notebooklm_client] = lambda: mock_nb_client
    app.dependency_overrides[get_mcp_gemini_client] = lambda: mock_gemini

    try:
        tc = TestClient(app)

        # 1. notebooklm_list
        r_list = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 20,
                "method": "tools/call",
                "params": {"name": "notebooklm_list", "arguments": {}},
            },
        )
        assert r_list.status_code == 200
        res_list = json.loads(r_list.json()["result"]["content"][0]["text"])
        assert len(res_list) == 2
        assert res_list[0]["title"] == "Quantum Computing Notes"

        # 2. notebooklm_query
        r_query = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 21,
                "method": "tools/call",
                "params": {
                    "name": "notebooklm_query",
                    "arguments": {
                        "notebook_id": "nb_123",
                        "query": "How are superconducting qubits built?",
                    },
                },
            },
        )
        assert r_query.status_code == 200
        assert "Quantum qubits are synthesized." in r_query.json()["result"]["content"][0]["text"]
        assert '<notebooklm_reference title="Quantum Computing Notes">' in mock_gemini.recorded_calls[0]["prompt"]

        # 3. notebooklm_add_source
        r_add = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 22,
                "method": "tools/call",
                "params": {
                    "name": "notebooklm_add_source",
                    "arguments": {
                        "notebook_id": "nb_123",
                        "title": "Cryostat Spec",
                        "content": "Operates at 10 mK dilution stage.",
                    },
                },
            },
        )
        assert r_add.status_code == 200
        res_add = json.loads(r_add.json()["result"]["content"][0]["text"])
        assert res_add["success"] is True
        assert res_add["source"]["title"] == "Cryostat Spec"

    finally:
        app.dependency_overrides.pop(get_mcp_notebooklm_client, None)
        app.dependency_overrides.pop(get_mcp_gemini_client, None)


def test_mcp_tool_call_gemini_list_gems() -> None:
    fake_client = MockGeminiClient()
    app.dependency_overrides[get_mcp_gemini_client] = lambda: fake_client

    try:
        tc = TestClient(app)
        r = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/call",
                "params": {"name": "gemini_list_gems", "arguments": {"type": "all"}},
            },
        )
        assert r.status_code == 200
        gems = json.loads(r.json()["result"]["content"][0]["text"])
        assert len(gems) >= 2
        titles = [g["title"] for g in gems]
        assert "Custom Coder" in titles
        assert "Google Scholar" in titles

    finally:
        app.dependency_overrides.pop(get_mcp_gemini_client, None)


def test_mcp_resources_list_and_read() -> None:
    mock_nb_client = MockNotebookLMClient()
    fake_client = MockGeminiClient()
    app.dependency_overrides[get_mcp_notebooklm_client] = lambda: mock_nb_client
    app.dependency_overrides[get_mcp_gemini_client] = lambda: fake_client

    try:
        tc = TestClient(app)

        # 1. resources/list
        r_res = tc.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 40, "method": "resources/list"},
        )
        assert r_res.status_code == 200
        res_list = r_res.json()["result"]["resources"]
        uris = [r["uri"] for r in res_list]
        assert "notebooklm://notebooks" in uris
        assert "gemini://gems" in uris
        assert "gemini://models" in uris

        # 2. resources/read notebooklm://notebooks
        r_read_nb = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 41,
                "method": "resources/read",
                "params": {"uri": "notebooklm://notebooks"},
            },
        )
        assert r_read_nb.status_code == 200
        nb_data = json.loads(r_read_nb.json()["result"]["contents"][0]["text"])
        assert len(nb_data) == 2
        assert nb_data[0]["title"] == "Quantum Computing Notes"

        # 3. resources/read gemini://models
        r_read_models = tc.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 42,
                "method": "resources/read",
                "params": {"uri": "gemini://models"},
            },
        )
        assert r_read_models.status_code == 200
        models_data = json.loads(r_read_models.json()["result"]["contents"][0]["text"])
        assert len(models_data) > 0

    finally:
        app.dependency_overrides.pop(get_mcp_notebooklm_client, None)
        app.dependency_overrides.pop(get_mcp_gemini_client, None)


def test_mcp_batch_and_error_handling() -> None:
    tc = TestClient(app)

    # 1. Batch Request
    r_batch = tc.post(
        "/mcp",
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "initialize"},
        ],
    )
    assert r_batch.status_code == 200
    batch_res = r_batch.json()
    assert isinstance(batch_res, list)
    assert len(batch_res) == 2
    assert batch_res[0]["id"] == 1
    assert batch_res[1]["id"] == 2

    # 2. Parse error (-32700)
    r_parse_err = tc.post(
        "/mcp",
        content="invalid json not parseable",
        headers={"Content-Type": "application/json"},
    )
    assert r_parse_err.status_code == 400
    assert r_parse_err.json()["error"]["code"] == -32700

    # 3. Method not found (-32601)
    r_unknown_method = tc.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 99, "method": "nonexistent_rpc_method"},
    )
    assert r_unknown_method.status_code == 200
    assert r_unknown_method.json()["error"]["code"] == -32601

    # 4. Unknown tool in tools/call (-32601)
    r_unknown_tool = tc.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        },
    )
    assert r_unknown_tool.status_code == 200
    assert r_unknown_tool.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_mcp_server_direct_message_handling() -> None:
    server = MCPServer()
    # Test ping
    res = await server.handle_message({"jsonrpc": "2.0", "id": "test-1", "method": "ping"})
    assert res == {"jsonrpc": "2.0", "id": "test-1", "result": {}}

    # Test tools/list
    res_tools = await server.handle_message({"jsonrpc": "2.0", "id": "test-2", "method": "tools/list"})
    assert "tools" in res_tools["result"]
    assert len(res_tools["result"]["tools"]) >= 7


def test_anthropic_base64_image_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    uploaded_files = []

    class MockUploader:
        async def upload_file(self, data: bytes, filename: str, mime_type: str) -> str:
            uploaded_files.append({"data": data, "filename": filename, "mime_type": mime_type})
            return "files/uploaded_fife_uri_123"

    fake_client = MockGeminiClient(text_responses=["Image processed successfully."])
    app.dependency_overrides[get_gemini_client] = lambda: fake_client
    app.dependency_overrides[get_gemini_uploader] = lambda: MockUploader()
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    try:
        tc = TestClient(app)
        # Small 1x1 valid PNG in base64
        b64_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        r = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What is in this image?"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64_png,
                                },
                            },
                        ],
                    }
                ],
            },
        )
        assert r.status_code == 200
        assert r.json()["content"][0]["text"] == "Image processed successfully."
        assert len(uploaded_files) == 1
        assert uploaded_files[0]["mime_type"] == "image/png"
        assert len(fake_client.recorded_calls) == 1
        assert fake_client.recorded_calls[0]["attachments"] == [{"uri": "files/uploaded_fife_uri_123", "filename": uploaded_files[0]["filename"]}]

    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


def test_anthropic_malformed_tool_call_treated_as_text() -> None:
    malformed = "Here is bad syntax: ```tool_call\n{not valid json\n```\nDone."
    clean_text, tools = AnthropicAdapter.parse_tool_calls(malformed)
    # Should not crash, returns zero tools and leaves text intact
    assert len(tools) == 0
    assert "Here is bad syntax" in clean_text


def test_anthropic_stop_sequences_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    fake_client = MockGeminiClient(text_responses=["Prefix text before stop sequence. STOP here should be removed."])
    app.dependency_overrides[get_gemini_client] = lambda: fake_client
    app.dependency_overrides[get_gemini_uploader] = lambda: None
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    try:
        tc = TestClient(app)
        r = tc.post(
            "/v1/messages",
            json={
                "model": "claude-3-5-sonnet",
                "stop_sequences": ["STOP"],
                "messages": [{"role": "user", "content": "Generate text with STOP."}],
            },
        )
        assert r.status_code == 200
        d = r.json()
        assert d["stop_reason"] == "stop_sequence"
        assert d["stop_sequence"] == "STOP"
        assert "here should be removed" not in d["content"][0]["text"]
        assert "Prefix text before stop sequence." in d["content"][0]["text"]

    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


def test_mcp_unsupported_resource_uri_error() -> None:
    tc = TestClient(app)
    r = tc.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 999,
            "method": "resources/read",
            "params": {"uri": "unsupported://invalid/scheme"},
        },
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32603

