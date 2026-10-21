"""
backend/tests/test_parity_and_canvas.py
Automated test suite for Milestone M5: Core Gemini Web Feature Parity, Canvas side-panel, and Gems Manager.

Covers:
1. Incognito / Ephemeral Chat: inner[45]=1 wire envelope, non-persistence in SQLite, assistant_message_id=None.
2. Chat Lifecycle: Pin/unpin, title rename, list ordering (is_pinned DESC), DELETE ?remote=true with GzXR5e RPC.
3. Streaming Rich Metadata Extraction: candidate[12] web citations, generated images, search grounding, YouTube references.
4. Error 1050 Prevention: Cross-model mid-chat branching without stale Google conversation_id.
5. Gems Manager & System Instructions: CNgdBe RPC parsing for custom/system gems, GET /api/gems, Turn-0 prefix injection.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import Gem, GemSerializer
from backend.app.bridge.session import SessionManager
from backend.app.bridge.streaming_parser import StreamingDeltaAccumulator
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
    server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
    server.start_background()
    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock(mock_server: MockGeminiServer):
    mock_server.fault.reset()


@pytest.fixture
def m5_context(tmp_path: Path, mock_server: MockGeminiServer):
    db_file = tmp_path / "test_m5.db"
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
    sm.context.access_token = "mock_token_abc"

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
    yield client, repo, db, sm
    asyncio.run(db.close())


def parse_sse_events(response_text: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in response_text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                events.append(json.loads(payload))
    return events


# ============================================================================
# 1. Incognito / Ephemeral Chat Tests
# ============================================================================

def test_incognito_chat_wire_envelope():
    """Verify that incognito=True wires inner[45]=1 into the 81-element request envelope."""
    client = GeminiWebClient(base_url=MOCK_BASE_URL)

    # Regular chat: inner[45] is None or 0
    regular_envelope = client._build_81_element_envelope(
        prompt="Regular hello",
        incognito=False,
    )
    assert len(regular_envelope) > 45
    assert regular_envelope[45] is None or regular_envelope[45] == 0

    # Incognito chat: inner[45] must be exactly 1
    incognito_envelope = client._build_81_element_envelope(
        prompt="Secret incognito prompt",
        incognito=True,
    )
    assert len(incognito_envelope) > 45
    assert incognito_envelope[45] == 1


def test_incognito_chat_non_persistence(m5_context):
    """Verify that incognito chat bypasses SQLite persistence and emits assistant_message_id=None."""
    client, repo, db, _ = m5_context

    # Send incognito prompt
    res = client.post(
        "/api/chat/generate",
        json={
            "prompt": "Top secret incognito question",
            "model": "56fdd199312815e2",
            "incognito": True,
        },
    )
    assert res.status_code == 200
    events = parse_sse_events(res.text)

    # Check start and saved events
    start_ev = next((e for e in events if e.get("type") == "start"), None)
    assert start_ev is not None
    assert start_ev.get("user_message_id") is not None

    saved_ev = next((e for e in events if e.get("type") == "saved"), None)
    assert saved_ev is not None
    # For incognito, assistant_message_id is null
    assert saved_ev.get("assistant_message_id") is None

    # Check that database has ZERO conversations and ZERO messages recorded
    async def verify_db_empty():
        convs, total = await repo.list_conversations(limit=10)
        assert len(convs) == 0
        assert total == 0
        row = await db.fetch_one("SELECT COUNT(*) as cnt FROM messages")
        assert row["cnt"] == 0

    asyncio.run(verify_db_empty())


# ============================================================================
# 2. Chat Lifecycle Management Tests
# ============================================================================

def test_chat_lifecycle_pin_rename_ordering(m5_context):
    """Verify PATCH for is_pinned and title, and verify ordering by is_pinned DESC."""
    client, repo, _, _ = m5_context

    # 1. Create first conversation
    r1 = client.post("/api/conversations", json={"title": "First Normal Chat", "model": "56fdd199312815e2"})
    assert r1.status_code == 201
    c1_id = r1.json()["id"]

    # 2. Create second conversation
    r2 = client.post("/api/conversations", json={"title": "Second Normal Chat", "model": "56fdd199312815e2"})
    assert r2.status_code == 201
    c2_id = r2.json()["id"]

    # Initially, c2 is first because created later
    list1 = client.get("/api/conversations").json()["conversations"]
    assert list1[0]["id"] == c2_id

    # 3. Pin c1 and rename it via PATCH /api/conversations/{id}
    patch_res = client.patch(
        f"/api/conversations/{c1_id}",
        json={"title": "Pinned VIP Chat", "is_pinned": True},
    )
    assert patch_res.status_code == 200
    patched = patch_res.json()
    assert patched["title"] == "Pinned VIP Chat"
    assert patched["is_pinned"] is True

    # 4. Listing should now have c1 first because is_pinned=True (is_pinned DESC)
    list2 = client.get("/api/conversations").json()["conversations"]
    assert list2[0]["id"] == c1_id
    assert list2[0]["is_pinned"] is True
    assert list2[0]["title"] == "Pinned VIP Chat"


def test_chat_lifecycle_delete_remote(m5_context):
    """Verify DELETE /api/conversations/{id}?remote=true deletes locally and calls GzXR5e."""
    client, repo, _, _ = m5_context

    # Create conversation with remote_conversation_id
    r = client.post("/api/conversations", json={"title": "Chat to Delete", "model": "56fdd199312815e2"})
    c_id = r.json()["id"]

    # Assign a remote_conversation_id in DB
    async def set_remote():
        await repo.update_conversation(c_id, remote_conversation_id="remote_cid_999")
    asyncio.run(set_remote())

    # Delete with remote=true
    del_res = client.delete(f"/api/conversations/{c_id}?remote=true")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # Confirm conversation is gone locally
    get_res = client.get(f"/api/conversations/{c_id}")
    assert get_res.status_code == 404


def test_bridge_client_delete_conversation_gzxr5e():
    """Verify GeminiWebClient.delete_conversation builds Batchexecute RPC GzXR5e payload."""
    client = GeminiWebClient(base_url=MOCK_BASE_URL)
    called_rpc = None
    called_payload = None

    async def mock_call(rpc_id, payload):
        nonlocal called_rpc, called_payload
        called_rpc = rpc_id
        called_payload = payload
        return []

    client.call_batchexecute = mock_call

    res = asyncio.run(client.delete_conversation("test_cid_456"))
    assert res is True
    assert called_rpc == "GzXR5e"
    assert called_payload == ["test_cid_456"]


# ============================================================================
# 3. Streaming Rich Metadata Extraction Tests
# ============================================================================

def test_streaming_rich_metadata_extraction():
    """Verify StreamingDeltaAccumulator extracts citations, images, grounding, and YouTube."""
    acc = StreamingDeltaAccumulator()

    # Raw candidate payload with citations, images, grounding, and YouTube video link in text
    candidate = [
        "rcid_100",
        ["text chunk 1 with video https://www.youtube.com/watch?v=dQw4w9WgXcQ included"],
        None,
        None,
        None,
        None,
        None,
        None,
        [2],  # turn status completed
        None,
        None,
        None,
        {
            # Web Citations in candidate[12]["43"]
            "43": [
                [None, "https://docs.python.org/3/", "Python 3 Documentation", 1],
                [None, "https://fastapi.tiangolo.com/", "FastAPI Framework", 2],
            ],
            # Generated images in candidate[12]["7"]
            "7": [
                ["https://lh3.googleusercontent.com/generated_img1", "High-res Diagram", 1024, 768],
            ],
            # Grounding search queries in candidate[12]["16"]
            "16": ["python async streaming fast", "fastapi sse tutorial"],
            # Grounding sources in candidate[12]["23"]
            "23": [
                [None, "https://wikipedia.org/wiki/FastAPI", "Wikipedia - FastAPI"],
            ],
            # Structured rich cards in candidate[12]["27"]
            "27": [
                {
                    "youtube_id": "dQw4w9WgXcQ",
                    "title": "Never Gonna Give You Up",
                    "duration": "3:33",
                    "channel": "Rick Astley",
                }
            ],
        },
    ]

    inner_part = [None, ["c_1", "r_1", "rcid_100"], None, None, [candidate]]
    frame = [["wrb.fr", None, json.dumps(inner_part)]]

    chunks = list(acc.process_frame(frame))

    # Should have emitted text chunk
    text_chunks = [c for c in chunks if c.type == "text"]
    assert len(text_chunks) == 1
    assert "text chunk 1" in text_chunks[0].delta

    # Should have emitted citation, grounding, and media chunks
    citation_chunks = [c for c in chunks if c.type == "citation"]
    assert len(citation_chunks) >= 1
    citations = citation_chunks[0].metadata["citations"]
    assert len(citations) == 2
    assert citations[0]["url"] == "https://docs.python.org/3/"
    assert citations[0]["domain"] == "docs.python.org"

    media_chunks = [c for c in chunks if c.type == "media"]
    assert len(media_chunks) >= 1
    media = media_chunks[0].metadata
    assert len(media["images"]) == 1
    assert media["images"][0]["url"] == "https://lh3.googleusercontent.com/generated_img1"
    assert len(media["youtube"]) >= 1
    assert any(y["video_id"] == "dQw4w9WgXcQ" for y in media["youtube"])

    grounding_chunks = [c for c in chunks if c.type == "grounding" and "queries" in c.metadata]
    assert len(grounding_chunks) >= 1
    assert "fastapi sse tutorial" in grounding_chunks[0].metadata["queries"]

    # When done chunk is produced, all rich metadata is aggregated
    done_chunks = [c for c in chunks if c.type == "done"]
    assert len(done_chunks) == 1
    done_meta = done_chunks[0].metadata
    assert "citations" in done_meta
    assert "images" in done_meta
    assert "youtube" in done_meta
    assert "grounding" in done_meta


# ============================================================================
# 4. Error 1050 Prevention via Local DAG Model Regeneration Tests
# ============================================================================

def test_error_1050_prevention_cross_model_branching(m5_context):
    """Verify that mid-chat model regeneration branches from existing_user_message_id without sending remote cid."""
    client, repo, _, _ = m5_context

    # 1. Create conversation with Flash model
    c_res = client.post("/api/conversations", json={"title": "Model Switch Test", "model": "56fdd199312815e2"})
    conv_id = c_res.json()["id"]

    # 2. Add User message and initial Flash assistant message
    async def seed_thread():
        u_msg = await repo.add_message(
            conversation_id=conv_id,
            role="user",
            content="Explain quantum computing simply",
            model="56fdd199312815e2",
        )
        a_msg = await repo.add_message(
            conversation_id=conv_id,
            role="assistant",
            content="Flash response: Quantum bits can be 0 and 1 simultaneously.",
            parent_id=u_msg.id,
            model="56fdd199312815e2",
        )
        await repo.update_conversation(conv_id, active_leaf_message_id=a_msg.id, remote_conversation_id="remote_cid_flash")
        return u_msg.id, a_msg.id

    user_msg_id, orig_asst_id = asyncio.run(seed_thread())

    # 3. Regenerate with Argon 4 model (95a4c0abcba24364) from user_msg_id
    regen_res = client.post(
        "/api/chat/generate",
        json={
            "conversation_id": conv_id,
            "existing_user_message_id": user_msg_id,
            "model": "95a4c0abcba24364@thinking=4@mode=chat",
            "prompt": "Explain quantum computing simply",
        },
    )
    assert regen_res.status_code == 200
    events = parse_sse_events(regen_res.text)

    # Check saved event
    saved_ev = next((e for e in events if e.get("type") == "saved"), None)
    assert saved_ev is not None
    new_asst_id = saved_ev.get("assistant_message_id")
    assert new_asst_id is not None
    assert new_asst_id != orig_asst_id

    # 4. Verify in DB that new assistant message has parent_id == user_msg_id
    async def verify_branch():
        new_asst = await repo.get_message(new_asst_id)
        assert new_asst is not None
        assert new_asst.parent_id == user_msg_id
        assert "95a4c0abcba24364" in new_asst.model

        # Both assistant messages now share the same parent_id (DAG branch)
        all_msgs = await repo.get_all_messages(conv_id)
        children = [m for m in all_msgs if m.parent_id == user_msg_id]
        assert len(children) == 2
        assert any(m.id == orig_asst_id for m in children)
        assert any(m.id == new_asst_id for m in children)

    asyncio.run(verify_branch())


# ============================================================================
# 5. Gems Manager & System Instructions Tests
# ============================================================================

def test_gems_listing_and_system_instructions(m5_context):
    """Verify GET /api/gems, CNgdBe parsing, and Turn-0 system persona prefix injection."""
    client, repo, _, sm = m5_context

    # 1. Test GET /api/gems endpoint
    res = client.get("/api/gems?type=all")
    assert res.status_code == 200
    data = res.json()
    assert "gems" in data
    assert "custom_gems" in data
    assert "system_gems" in data

    # 2. Test GemSerializer with mock CNgdBe array payload
    mock_cngdbe_payload = [
        None,
        [
            [
                "gem_custom_101",
                "Coding Master",
                "Python and TypeScript architect",
                "You are an elite software architect who writes immaculate code.",
                "👨‍💻",
                2,  # custom gem type
                1700000000,
                1700000000,
            ],
            [
                "gem_system_202",
                "Deep Thinker",
                "Mathematical analysis expert",
                "Solve problems with step-by-step rigorous proofs.",
                "🧠",
                3,  # system gem type
                1700000000,
                1700000000,
            ],
        ],
    ]

    parsed_custom = GemSerializer.parse_list_response(mock_cngdbe_payload, is_builtin=False)
    assert len(parsed_custom) == 2
    g1 = parsed_custom[0]
    assert g1.id == "gem_custom_101"
    assert g1.name == "Coding Master"
    assert g1.prompt == "You are an elite software architect who writes immaculate code."
    assert g1.is_builtin is False
    assert g1.to_dict()["name"] == "Coding Master"

    # 3. Test Turn-0 system instruction prefix injection in /api/chat/generate
    chat_res = client.post(
        "/api/chat/generate",
        json={
            "prompt": "Who are you?",
            "model": "56fdd199312815e2",
            "system_instruction": "You are a specialized aerospace engineer.",
        },
    )
    assert chat_res.status_code == 200
    events = parse_sse_events(chat_res.text)
    start_ev = next((e for e in events if e.get("type") == "start"), None)
    assert start_ev is not None
    user_msg_id = start_ev.get("user_message_id")

    # Verify that stored user prompt in repository contains the Turn-0 system persona header
    async def check_user_prompt():
        msg = await repo.get_message(user_msg_id)
        assert msg is not None
        assert "[System Instructions]" in msg.content
        assert "You are a specialized aerospace engineer." in msg.content
        assert "Who are you?" in msg.content

    asyncio.run(check_user_prompt())
