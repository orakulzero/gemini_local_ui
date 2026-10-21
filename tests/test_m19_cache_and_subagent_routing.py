import pytest
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.anthropic.adapter import AnthropicAdapter
from backend.app.anthropic.routes import get_gemini_client
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.openai.conversation_cache import ConversationCache


class MockM19GeminiClient:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def generate_stream(self, **kwargs: Any) -> AsyncIterator[StreamChunk]:
        self.calls.append(kwargs)
        yield StreamChunk(type="text", delta="Subagent routed response.")
        yield StreamChunk(
            type="done",
            metadata={"cid": "cid_m19", "rid": "rid_m19", "rcid": "rcid_m19"},
        )

    async def close(self) -> None:
        pass


def test_sqlite_conversation_cache_survives_restart(tmp_path: Path):
    db_file = tmp_path / "test_conv_cache.db"
    cache_instance_1 = ConversationCache(max_size=50, db_path=db_file)

    turn1_messages = [{"role": "user", "content": "Hello M19 SQLite Cache"}]
    key = cache_instance_1.remember(
        turn1_messages,
        {"role": "assistant", "content": "Hi there!"},
        {"cid": "c_sqlite_1", "rid": "r_sqlite_1", "rcid": "rc_sqlite_1"},
    )
    assert key is not None
    assert db_file.exists()

    # Simulate server restart by instantiating a brand-new ConversationCache pointing to the same SQLite DB
    cache_instance_2 = ConversationCache(max_size=50, db_path=db_file)
    assert len(cache_instance_2) == 0  # RAM is initially cold

    turn2_messages = [
        {"role": "user", "content": "Hello M19 SQLite Cache"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "Follow-up after restart"},
    ]
    matched_n, chat_state = cache_instance_2.find(turn2_messages)
    assert matched_n == 2
    assert chat_state == {
        "cid": "c_sqlite_1",
        "rid": "r_sqlite_1",
        "rcid": "rc_sqlite_1",
    }
    assert len(cache_instance_2) == 1  # Warmed into RAM LRU


@pytest.mark.asyncio
async def test_historical_tool_result_compaction_on_cache_miss():
    messages: List[Dict[str, Any]] = []
    # Build 8 prior turns (4 user + 4 assistant) plus 1 final user turn = 9 turns total
    for i in range(4):
        big_output = f"LINE_{i}_" + ("X" * 1200)
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"toolu_old_{i}",
                        "content": big_output,
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": f"Ack turn {i}"}],
            }
        )

    messages.append({"role": "user", "content": "Final turn question"})

    final_prompt, history, _ = await AnthropicAdapter.extract_turns_and_attachments(messages)
    assert len(history) == 8
    # Early tool_result turn (idx 0) should be compacted
    assert "...[truncated historical tool_result]" in history[0]["content"]
    assert len(history[0]["content"]) < 700
    # Recent tool_result turn within last 4 prior turns (idx 6) should remain full length
    assert "...[truncated historical tool_result]" not in history[6]["content"]
    assert len(history[6]["content"]) > 1000


def test_subagent_auto_routing_flash_vs_argon():
    mock_client = MockM19GeminiClient()
    app.dependency_overrides[get_gemini_client] = lambda: mock_client
    try:
        with TestClient(app) as client:
            # 1. Request carrying explorer-scout system prompt (even if parent CLI sends claude-opus-5-5)
            scout_resp = client.post(
                "/v1/messages",
                json={
                    "model": "claude-opus-5-5",
                    "max_tokens": 1024,
                    "system": "You are the **Codebase Explorer Scout** for `gemini_local_ui`.\nInspect files.",
                    "messages": [{"role": "user", "content": "Map backend routers"}],
                    "stream": False,
                },
            )
            assert scout_resp.status_code == 200
            assert scout_resp.headers.get("X-Gemini-Subagent") == "explorer-scout"
            assert scout_resp.headers.get("X-Gemini-Model-Resolved") == "56fdd199312815e2"
            assert scout_resp.headers.get("X-Gemini-Gem-Id") == "gem_cc_explorer-scout"
            assert mock_client.calls[-1]["model"] == "56fdd199312815e2"
            assert mock_client.calls[-1]["gem_id"] == "gem_cc_explorer-scout"

            # 2. Request carrying argon-architect system prompt -> routes to Argon + feature_mode=40
            arch_resp = client.post(
                "/v1/messages",
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 1024,
                    "system": [
                        {
                            "type": "text",
                            "text": "You are the **Argon Chief Architect** for `gemini_local_ui`.",
                        }
                    ],
                    "messages": [{"role": "user", "content": "Review architecture"}],
                    "stream": False,
                },
            )
            assert arch_resp.status_code == 200
            assert arch_resp.headers.get("X-Gemini-Subagent") == "argon-architect"
            assert arch_resp.headers.get("X-Gemini-Model-Resolved") == "95a4c0abcba24364"
            assert arch_resp.headers.get("X-Gemini-Gem-Id") == "gem_cc_argon-architect"
            assert mock_client.calls[-1]["model"] == "95a4c0abcba24364"
            assert mock_client.calls[-1]["gem_id"] == "gem_cc_argon-architect"
            assert mock_client.calls[-1].get("feature_mode") == 40
    finally:
        app.dependency_overrides.pop(get_gemini_client, None)
