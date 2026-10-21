"""
backend/tests/test_chat_stream.py
Integration test suite for POST /api/chat/generate SSE streaming generation endpoint.
Validates:
1. Request validation (empty prompt returns HTTP 400).
2. Streaming response with SSE formatted lines (`data: {...}`).
3. Events lifecycle: `start`, `thought`, `text`, `saved`, `done`.
4. Multimodal attachment persistence and bridge forwarding.
5. Error propagation and failed turn DB persistence.
6. Multi-turn continuation and parent_message_id branching.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine

MOCK_PORT = 8999
MOCK_HOST = "127.0.0.1"
MOCK_BASE_URL = f"http://{MOCK_HOST}:{MOCK_PORT}"


@pytest.fixture(scope="module")
def mock_server():
    """Starts deterministic mock Gemini web server in background thread."""
    server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
    server.start_background()
    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock(mock_server: MockGeminiServer):
    """Resets mock faults and recorded requests between tests."""
    mock_server.fault.reset()


@pytest.fixture
def chat_context(tmp_path: Path, mock_server: MockGeminiServer):
    """Initializes isolated database, repository, session manager, and FastAPI test client."""
    db_file = tmp_path / "test_chat_stream.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(db.init_schema(schema_path))

    repo = ConversationRepository(db)
    search_eng = SearchEngine(db)

    sm = SessionManager(base_url=MOCK_BASE_URL)
    sm.context.cookies = {
        "__Secure-1PSID": "mock_psid_valid_test_token_12345",
        "__Secure-1PSIDTS": "mock_psidts_initial_timestamp_67890",
    }
    sm.context.access_token = "mock_snlm0e_access_token_abc123"

    import backend.app.main as main_mod
    main_mod.db = db
    main_mod.repository = repo
    main_mod.search_engine = search_eng
    main_mod.session_manager = sm

    app.state.db = db
    app.state.repository = repo
    app.state.search_engine = search_eng
    app.state.session_manager = sm

    client = TestClient(app, base_url="http://testserver")
    yield client, repo, db
    asyncio.run(db.close())


def parse_sse_events(response_text: str) -> List[Dict[str, Any]]:
    """Extracts JSON event objects from standard SSE data lines."""
    events: List[Dict[str, Any]] = []
    for line in response_text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                events.append(json.loads(payload))
    return events


# ============================================================================
# 1. Request Validation Tests
# ============================================================================

def test_chat_generate_validation_empty_prompt_and_no_attachments(chat_context):
    """Verifies that an empty prompt without attachments is rejected with HTTP 400."""
    client, repo, _ = chat_context

    # Case 1: Empty string prompt
    res1 = client.post("/api/chat/generate", json={"prompt": ""})
    assert res1.status_code == 400
    assert "Prompt or attachments must be provided" in res1.json()["detail"]

    # Case 2: Whitespace-only prompt
    res2 = client.post("/api/chat/generate", json={"prompt": "    \n\t  "})
    assert res2.status_code == 400
    assert "Prompt or attachments must be provided" in res2.json()["detail"]

    # Case 3: Empty attachments array with empty prompt
    res3 = client.post("/api/chat/generate", json={"prompt": "", "attachments": []})
    assert res3.status_code == 400
    assert "Prompt or attachments must be provided" in res3.json()["detail"]


def test_chat_generate_whitespace_prompt_with_valid_attachment(chat_context):
    """Verifies that whitespace prompt is accepted when attachments are present."""
    client, repo, _ = chat_context

    payload = {
        "prompt": "   ",
        "attachments": [
            {
                "uri": "/contrib/mock_test_image.png",
                "filename": "mock_test_image.png",
                "mime_type": "image/png",
                "file_size": 1024,
            }
        ],
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")

    events = parse_sse_events(res.text)
    types = [e["type"] for e in events]
    assert "start" in types
    assert "text" in types
    assert "done" in types
    assert "saved" in types


# ============================================================================
# 2. SSE Streaming Lifecycle Events & Persistence Tests
# ============================================================================

def test_chat_generate_streaming_events_lifecycle(chat_context):
    """
    Verifies full SSE event lifecycle:
    `start` -> `thought` -> `text` -> `done` -> `saved`.
    Confirms SQLite persistence of user turn and assistant completion.
    """
    client, repo, _ = chat_context

    payload = {
        "prompt": "Explain Windows DPAPI architecture",
        "model": "gemini-2.0-flash-thinking",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]

    events = parse_sse_events(res.text)
    assert len(events) >= 4

    # 1. Event 'start'
    start_event = events[0]
    assert start_event["type"] == "start"
    assert "conversation_id" in start_event
    assert start_event["user_message_id"] is not None
    conv_id = start_event["conversation_id"]
    user_msg_id = start_event["user_message_id"]

    # 2. Events 'thought' and 'text'
    thought_deltas = [e["delta"] for e in events if e["type"] == "thought"]
    text_deltas = [e["delta"] for e in events if e["type"] == "text"]
    assert len(thought_deltas) > 0, "Expected at least one thought chunk"
    assert len(text_deltas) > 0, "Expected at least one text chunk"

    full_thought = "".join(thought_deltas)
    full_text = "".join(text_deltas)
    assert "cryptographic vaults" in full_thought or "DPAPI" in full_thought
    assert "DPAPI" in full_text

    # 3. Event 'done'
    done_events = [e for e in events if e["type"] == "done"]
    assert len(done_events) == 1

    # 4. Event 'saved'
    saved_events = [e for e in events if e["type"] == "saved"]
    assert len(saved_events) == 1
    asst_msg_id = saved_events[0]["assistant_message_id"]
    assert asst_msg_id is not None
    assert asst_msg_id > user_msg_id

    # 5. Verify SQLite Persistence
    thread = asyncio.run(repo.get_active_thread(conv_id, leaf_message_id=asst_msg_id))
    assert len(thread) == 2

    user_msg = thread[0]
    asst_msg = thread[1]

    assert user_msg.id == user_msg_id
    assert user_msg.role == "user"
    assert user_msg.content == "Explain Windows DPAPI architecture"
    assert user_msg.parent_id is None

    assert asst_msg.id == asst_msg_id
    assert asst_msg.role == "assistant"
    assert asst_msg.parent_id == user_msg_id
    assert asst_msg.thoughts is not None
    assert "cryptographic vaults" in asst_msg.thoughts or "DPAPI" in asst_msg.thoughts
    assert "DPAPI" in asst_msg.content
    assert asst_msg.is_error is False


# ============================================================================
# 3. Branching & Parent Message ID Continuity Tests
# ============================================================================

def test_chat_generate_branching_with_parent_message_id(chat_context):
    """Verifies that parent_message_id attaches the new turn to an earlier node in DAG."""
    client, repo, _ = chat_context

    # 1. First turn: Root Q1 -> A1
    res1 = client.post("/api/chat/generate", json={"prompt": "Turn 1 Question"})
    events1 = parse_sse_events(res1.text)
    conv_id = events1[0]["conversation_id"]
    asst1_id = next(e["assistant_message_id"] for e in events1 if e["type"] == "saved")

    # 2. Second turn: Branching off A1
    payload2 = {
        "conversation_id": conv_id,
        "parent_message_id": asst1_id,
        "prompt": "Turn 2 Followup Question",
    }
    res2 = client.post("/api/chat/generate", json=payload2)
    assert res2.status_code == 200
    events2 = parse_sse_events(res2.text)
    user2_id = events2[0]["user_message_id"]
    asst2_id = next(e["assistant_message_id"] for e in events2 if e["type"] == "saved")

    # Verify parent linking in SQLite
    user2_msg = asyncio.run(repo.get_message(user2_id))
    assert user2_msg is not None
    assert user2_msg.parent_id == asst1_id

    asst2_msg = asyncio.run(repo.get_message(asst2_id))
    assert asst2_msg is not None
    assert asst2_msg.parent_id == user2_id

    # Active leaf should be updated to asst2_id
    conv = asyncio.run(repo.get_conversation(conv_id))
    assert conv.active_leaf_message_id == asst2_id


def test_chat_generate_with_existing_user_message_id(chat_context):
    """Verifies that existing_user_message_id streams an assistant response without duplicating the user turn."""
    client, repo, _ = chat_context

    # 1. Create initial turn Q1 -> A1
    res1 = client.post("/api/chat/generate", json={"prompt": "Original question"})
    events1 = parse_sse_events(res1.text)
    conv_id = events1[0]["conversation_id"]
    user1_id = events1[0]["user_message_id"]

    # 2. Edit user1_id via /api/conversations/{id}/messages/{msg_id}/edit
    edit_res = client.post(
        f"/api/conversations/{conv_id}/messages/{user1_id}/edit",
        json={"content": "Edited fork question", "model": "gemini-2.5-flash"},
    )
    assert edit_res.status_code == 200
    created_fork_msg = edit_res.json()["created_message"]

    # 3. Generate assistant reply for created_fork_msg using existing_user_message_id
    res2 = client.post(
        "/api/chat/generate",
        json={
            "conversation_id": conv_id,
            "parent_message_id": created_fork_msg.get("parent_id"),
            "existing_user_message_id": created_fork_msg["id"],
            "prompt": "",
            "model": "gemini-2.5-flash",
        },
    )
    assert res2.status_code == 200
    events2 = parse_sse_events(res2.text)
    assert events2[0]["user_message_id"] == created_fork_msg["id"]
    asst_fork_id = next(e["assistant_message_id"] for e in events2 if e["type"] == "saved")

    # Verify active thread has exactly 2 messages (edited user + new assistant), no duplicate user turn
    thread = asyncio.run(repo.get_active_thread(conv_id, leaf_message_id=asst_fork_id))
    assert len(thread) == 2
    assert thread[0].id == created_fork_msg["id"]
    assert thread[0].content == "Edited fork question"
    assert thread[1].id == asst_fork_id
    assert thread[1].parent_id == created_fork_msg["id"]


# ============================================================================
# 4. Multimodal Attachments Handling Tests
# ============================================================================

def test_chat_generate_attachments_persistence_and_forwarding(chat_context, mock_server: MockGeminiServer):
    """Verifies that attachments are saved to SQLite and forwarded to Gemini bridge."""
    client, repo, _ = chat_context

    payload = {
        "prompt": "Inspect this diagram attachment",
        "attachments": [
            {
                "uri": "/contrib_service/ttl_1d/mock_uploaded_resource_hash_897123",
                "filename": "diagram.png",
                "mime_type": "image/png",
                "file_size": 24500,
                "storage_path": "attachments/diagram.png",
            }
        ],
    }

    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200
    events = parse_sse_events(res.text)
    user_msg_id = events[0]["user_message_id"]

    # Verify attachment in SQLite repository
    user_msg = asyncio.run(repo.get_message(user_msg_id))
    assert user_msg is not None
    assert len(user_msg.attachments) == 1
    att = user_msg.attachments[0]
    assert att.filename == "diagram.png"
    assert att.mime_type == "image/png"
    assert att.size_bytes == 24500
    assert att.uri == "/contrib_service/ttl_1d/mock_uploaded_resource_hash_897123"

    # Verify request arrived at mock server
    recorded = mock_server.fault.recorded_requests
    stream_requests = [r for r in recorded if "StreamGenerate" in r["path"]]
    assert len(stream_requests) > 0


# ============================================================================
# 5. Error Handling & Failed Turn Persistence Tests
# ============================================================================

def test_chat_generate_stream_error_propagation_and_db_state(chat_context):
    """Verifies that backend stream errors emit an SSE 'error' event and mark the turn as error in DB."""
    client, repo, _ = chat_context

    # [TRIGGER_OVERLOAD] triggers error frame code 1013 from MockGeminiServer
    payload = {
        "prompt": "Test error trigger [TRIGGER_OVERLOAD]",
    }
    res = client.post("/api/chat/generate", json=payload)
    assert res.status_code == 200
    events = parse_sse_events(res.text)

    types = [e["type"] for e in events]
    assert "start" in types
    assert "error" in types

    err_event = next(e for e in events if e["type"] == "error")
    assert "error" in err_event
    assert len(err_event["error"]) > 0

    # Verify assistant turn marked as is_error in SQLite
    conv_id = events[0]["conversation_id"]
    user_msg_id = events[0]["user_message_id"]

    thread = asyncio.run(repo.get_active_thread(conv_id))
    # Thread should contain user message and assistant error message
    assert len(thread) >= 2
    err_asst_msg = next((m for m in thread if m.role == "assistant" and m.parent_id == user_msg_id), None)
    assert err_asst_msg is not None
    assert err_asst_msg.is_error is True
    assert "Error generating response" in err_asst_msg.content
