"""
tests/test_m14_openai_stateful_tools_images.py
Verification suite for Milestone M14 (Step 3):
  - R1: Stateless-to-Stateful OpenAI Conversation Prefix Cache (SHA-256)
  - R2: OpenAI Function / Tool Calling (non-streaming & SSE streaming + multi-turn tool results)
  - R3: Local Image Proxy (`GET /files/{name}`) & `gg-dl` Markdown URL Rewriting
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient

from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.main import app
from backend.app.openai import conversation_cache
from backend.app.openai.adapter import OpenAIAdapter
from backend.app.openai.conversation_cache import ConversationCache, history_key
from backend.app.openai.image_store import NAME_RE, link_images, read_image, save_image
from backend.app.openai.routes import get_gemini_client, get_gemini_uploader
from backend.app.openai.schemas import ChatCompletionMessageParam


# ============================================================================
# R1: Conversation Prefix Cache Unit & Integration Tests
# ============================================================================


def test_history_key_deterministic_across_dict_and_pydantic() -> None:
    msgs_dict = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Hello"},
    ]
    msgs_pydantic = [
        ChatCompletionMessageParam(role="system", content="Be concise."),
        ChatCompletionMessageParam(role="user", content="Hello"),
    ]
    assert history_key(msgs_dict) == history_key(msgs_pydantic)


def test_conversation_cache_prefix_find_and_lru_eviction() -> None:
    cache = ConversationCache(max_size=3)
    m1 = [{"role": "user", "content": "Turn 1"}]
    cache.remember(m1, "Reply 1", {"cid": "c1", "rid": "r1", "rcid": "rc1"})

    # Turn 2 transcript has [user1, assistant1, user2] -> prefix [:2] matches!
    m2 = [
        {"role": "user", "content": "Turn 1"},
        {"role": "assistant", "content": "Reply 1"},
        {"role": "user", "content": "Turn 2"},
    ]
    matched_n, chat = cache.find(m2)
    assert matched_n == 2
    assert chat == {"cid": "c1", "rid": "r1", "rcid": "rc1"}

    # Test LRU eviction when exceeding max_size=3
    cache.remember([{"role": "user", "content": "A"}], "RA", {"cid": "cA", "rid": "rA", "rcid": "rcA"})
    cache.remember([{"role": "user", "content": "B"}], "RB", {"cid": "cB", "rid": "rB", "rcid": "rcB"})
    cache.remember([{"role": "user", "content": "C"}], "RC", {"cid": "cC", "rid": "rC", "rcid": "rcC"})
    assert len(cache) == 3
    # c1 should have been evicted
    n_evicted, chat_evicted = cache.find(m2)
    assert n_evicted == 0
    assert chat_evicted is None


def test_openai_route_stateful_continuation_sends_only_delta_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    recorded_calls: List[Dict[str, Any]] = []

    class FakeGeminiClient:
        async def generate_stream(
            self,
            prompt: str,
            model: str = "gemini-2.5-pro",
            gem_id: Optional[str] = None,
            attachments: Optional[List[Any]] = None,
            history: Optional[List[Any]] = None,
            **kwargs: Any,
        ) -> AsyncIterator[StreamChunk]:
            recorded_calls.append(
                {
                    "prompt": prompt,
                    "model": model,
                    "cid": kwargs.get("cid"),
                    "rid": kwargs.get("rid"),
                    "rcid": kwargs.get("rcid"),
                    "history": history,
                }
            )
            turn_num = len(recorded_calls)
            yield StreamChunk(type="text", delta=f"Assistant reply {turn_num}")
            yield StreamChunk(
                type="done",
                delta="",
                metadata={
                    "cid": "c_stateful_99",
                    "rid": f"r_turn_{turn_num}",
                    "rcid": f"rc_turn_{turn_num}",
                },
            )

        async def close(self) -> None:
            pass

    fake_client = FakeGeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: fake_client
    app.dependency_overrides[get_gemini_uploader] = lambda: None
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    try:
        tc = TestClient(app)
        # Turn 1: fresh conversation
        r1 = tc.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-4-argon",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "My secret number is 42."},
                ],
            },
        )
        assert r1.status_code == 200
        reply_1 = r1.json()["choices"][0]["message"]["content"]
        assert reply_1 == "Assistant reply 1"
        assert len(recorded_calls) == 1
        assert recorded_calls[0]["cid"] is None
        assert "My secret number is 42." in recorded_calls[0]["prompt"]

        # Turn 2: OpenAI client resends full history + new user message
        r2 = tc.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-4-argon",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "My secret number is 42."},
                    {"role": "assistant", "content": reply_1},
                    {"role": "user", "content": "What was my secret number?"},
                ],
            },
        )
        assert r2.status_code == 200
        reply_2 = r2.json()["choices"][0]["message"]["content"]
        assert reply_2 == "Assistant reply 2"
        assert len(recorded_calls) == 2

        # Verify Turn 2 resumed (cid, rid, rcid) and sent ONLY the delta user message!
        assert recorded_calls[1]["cid"] == "c_stateful_99"
        assert recorded_calls[1]["rid"] == "r_turn_1"
        assert recorded_calls[1]["rcid"] == "rc_turn_1"
        assert recorded_calls[1]["prompt"] == "What was my secret number?"
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


# ============================================================================
# R2: OpenAI Function / Tool Calling Tests
# ============================================================================


def test_build_tools_system_instruction_and_parse_tool_calls() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather in a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ]

    inst_auto = OpenAIAdapter.build_tools_system_instruction(tools, tool_choice="auto")
    assert inst_auto is not None
    assert "### get_weather" in inst_auto
    assert "```tool_call" in inst_auto

    inst_none = OpenAIAdapter.build_tools_system_instruction(tools, tool_choice="none")
    assert inst_none is None

    inst_forced = OpenAIAdapter.build_tools_system_instruction(
        tools, tool_choice={"type": "function", "function": {"name": "get_weather"}}
    )
    assert inst_forced is not None
    assert "You MUST call the tool `get_weather`" in inst_forced

    model_output = (
        "Let me check the weather for you.\n"
        "```tool_call\n"
        '{"name": "get_weather", "arguments": {"city": "Tokyo"}}\n'
        "```"
    )
    clean_text, parsed_calls = OpenAIAdapter.parse_tool_calls(model_output)
    assert clean_text == "Let me check the weather for you."
    assert len(parsed_calls) == 1
    assert parsed_calls[0]["type"] == "function"
    assert parsed_calls[0]["function"]["name"] == "get_weather"
    assert json.loads(parsed_calls[0]["function"]["arguments"]) == {"city": "Tokyo"}


def test_parse_tool_calls_json_fence_fallback() -> None:
    text = '''Here is some text with normal json:
```json
{"normal": "data"}
```
And a tool call:
```json
{"name": "get_weather", "arguments": {"city": "London"}}
```
'''
    clean_text, calls = OpenAIAdapter.parse_tool_calls(text)
    assert '{"normal": "data"}' in clean_text
    assert "get_weather" not in clean_text
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "get_weather"
    assert json.loads(calls[0]["function"]["arguments"]) == {"city": "London"}


def test_openai_route_tool_calling_non_streaming_and_streaming(monkeypatch: pytest.MonkeyPatch) -> None:
    conversation_cache.clear()
    recorded_prompts: List[Dict[str, Any]] = []

    class ToolMockGeminiClient:
        async def generate_stream(
            self,
            prompt: str,
            model: str = "gemini-2.5-pro",
            **kwargs: Any,
        ) -> AsyncIterator[StreamChunk]:
            recorded_prompts.append({"prompt": prompt, "cid": kwargs.get("cid")})
            if len(recorded_prompts) == 1:
                yield StreamChunk(
                    type="text",
                    delta='```tool_call\n{"name": "get_weather", "arguments": {"city": "Berlin"}}\n```',
                )
                yield StreamChunk(
                    type="done",
                    delta="",
                    metadata={"cid": "c_tool_1", "rid": "r_tool_1", "rcid": "rc_tool_1"},
                )
            elif len(recorded_prompts) == 2:
                yield StreamChunk(type="text", delta="The weather in Berlin is 21°C and sunny.")
                yield StreamChunk(
                    type="done",
                    delta="",
                    metadata={"cid": "c_tool_1", "rid": "r_tool_2", "rcid": "rc_tool_2"},
                )
            else:
                yield StreamChunk(
                    type="text",
                    delta='```tool_call\n{"name": "get_weather", "arguments": {"city": "Paris"}}\n```',
                )
                yield StreamChunk(
                    type="done",
                    delta="",
                    metadata={"cid": "c_tool_stream", "rid": "r_ts", "rcid": "rc_ts"},
                )

        async def close(self) -> None:
            pass

    app.dependency_overrides[get_gemini_client] = lambda: ToolMockGeminiClient()
    app.dependency_overrides[get_gemini_uploader] = lambda: None
    monkeypatch.setattr(app.state, "session_manager", None, raising=False)

    tools_spec = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Fetch weather",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    ]

    try:
        tc = TestClient(app)
        # 1. Non-streaming tool call
        resp1 = tc.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-4-argon",
                "messages": [{"role": "user", "content": "Weather in Berlin?"}],
                "tools": tools_spec,
            },
        )
        assert resp1.status_code == 200
        data1 = resp1.json()
        choice1 = data1["choices"][0]
        assert choice1["finish_reason"] == "tool_calls"
        assert choice1["message"]["content"] is None
        t_calls = choice1["message"]["tool_calls"]
        assert len(t_calls) == 1
        assert t_calls[0]["function"]["name"] == "get_weather"
        assert json.loads(t_calls[0]["function"]["arguments"]) == {"city": "Berlin"}

        # 2. Follow-up turn with tool result -> resumes cid="c_tool_1"
        resp2 = tc.post(
            "/v1/chat/completions",
            json={
                "model": "gemini-4-argon",
                "messages": [
                    {"role": "user", "content": "Weather in Berlin?"},
                    choice1["message"],
                    {
                        "role": "tool",
                        "tool_call_id": t_calls[0]["id"],
                        "name": "get_weather",
                        "content": '{"temp_c": 21, "condition": "sunny"}',
                    },
                ],
                "tools": tools_spec,
            },
        )
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["choices"][0]["finish_reason"] == "stop"
        assert "21°C" in data2["choices"][0]["message"]["content"]
        assert recorded_prompts[1]["cid"] == "c_tool_1"
        assert "[Tool result for get_weather]" in recorded_prompts[1]["prompt"]

        # 3. Streaming SSE tool call
        with tc.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "gemini-4-argon",
                "stream": True,
                "messages": [{"role": "user", "content": "Weather in Paris?"}],
                "tools": tools_spec,
            },
        ) as s_resp:
            assert s_resp.status_code == 200
            sse_Objects = []
            for line in s_resp.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    sse_Objects.append(json.loads(line[6:]))

            tool_chunks = [
                c for c in sse_Objects if c["choices"][0]["delta"].get("tool_calls")
            ]
            assert len(tool_chunks) == 1
            stream_tc = tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
            assert stream_tc["index"] == 0
            assert stream_tc["function"]["name"] == "get_weather"
            assert json.loads(stream_tc["function"]["arguments"]) == {"city": "Paris"}
            assert sse_Objects[-1]["choices"][0]["finish_reason"] == "tool_calls"
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
        app.dependency_overrides.pop(get_gemini_uploader, None)
        conversation_cache.clear()


# ============================================================================
# R3: Local Image Proxy (`GET /files/{name}`) & `gg-dl` Rewriting Tests
# ============================================================================


def test_image_store_save_read_and_path_traversal_guard(tmp_path: Path) -> None:
    png_bytes = b"\x89PNG\r\n\x1a\nfakepngcontent"
    saved_name = save_image(png_bytes, mime="image/png", data_dir=tmp_path)
    assert NAME_RE.match(saved_name) is not None
    assert saved_name.endswith(".png")

    loaded = read_image(saved_name, data_dir=tmp_path)
    assert loaded is not None
    assert loaded[0] == png_bytes
    assert loaded[1] == "image/png"

    # Path traversal and invalid filename attempts must return None
    assert read_image("../../vault.enc", data_dir=tmp_path) is None
    assert read_image("../images/" + saved_name, data_dir=tmp_path) is None
    assert read_image("not_a_32_char_hex.png", data_dir=tmp_path) is None
    assert read_image("a" * 32 + ".exe", data_dir=tmp_path) is None


def test_link_images_rewrites_gg_dl_and_appends_unreferenced() -> None:
    raw_text = "Here is your render: https://lh3.googleusercontent.com/gg-dl/AOI_d_12345"
    images = [
        {
            "url": "https://lh3.googleusercontent.com/gg-dl/AOI_d_12345",
            "file": "0123456789abcdef0123456789abcdef.png",
            "title": "Cyberpunk City",
        },
        {
            "url": "https://lh3.googleusercontent.com/gg-dl/AOI_d_67890",
            "file": "fedcba9876543210fedcba9876543210.jpg",
            "title": "Bonus Sketch",
        },
    ]
    rewritten = link_images(raw_text, images, base_url="http://127.0.0.1:8000")
    assert "https://lh3.googleusercontent.com/gg-dl/AOI_d_12345" not in rewritten
    assert "![Cyberpunk City](http://127.0.0.1:8000/files/0123456789abcdef0123456789abcdef.png)" in rewritten
    assert "![Bonus Sketch](http://127.0.0.1:8000/files/fedcba9876543210fedcba9876543210.jpg)" in rewritten


def test_files_endpoint_serves_saved_image_and_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.state, "image_data_dir", tmp_path, raising=False)
    jpg_bytes = b"\xff\xd8\xff\xe0fakejpegbytes"
    fname = save_image(jpg_bytes, mime="image/jpeg", data_dir=tmp_path)

    tc = TestClient(app)
    resp_ok = tc.get(f"/files/{fname}")
    assert resp_ok.status_code == 200
    assert resp_ok.content == jpg_bytes
    assert resp_ok.headers["content-type"] == "image/jpeg"

    resp_bad = tc.get("/files/not_valid_uuid.png")
    assert resp_bad.status_code == 404

    resp_missing = tc.get(f"/files/{'a' * 32}.png")
    assert resp_missing.status_code == 404
