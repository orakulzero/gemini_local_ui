"""
backend/tests/test_conversations_api.py
Integration tests for FastAPI conversation REST endpoints, DAG branching, export, and search.
"""

from __future__ import annotations

import json
from pathlib import Path
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
    server = MockGeminiServer(host=MOCK_HOST, port=MOCK_PORT)
    server.start_background()
    yield server
    server.stop_background()


@pytest.fixture(autouse=True)
def reset_mock(mock_server: MockGeminiServer):
    mock_server.fault.reset()


@pytest.fixture
def api_client(tmp_path: Path, mock_server: MockGeminiServer) -> TestClient:
    db_file = tmp_path / "test_api.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    import asyncio
    asyncio.run(db.init_schema(schema_path))

    repo = ConversationRepository(db)
    search_eng = SearchEngine(db)

    sm = SessionManager(base_url=MOCK_BASE_URL)
    sm.context.cookies = {"__Secure-1PSID": "mock_psid_valid_test_token_12345"}
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
    yield client
    asyncio.run(db.close())


def test_conversations_crud_endpoints(api_client: TestClient):
    # 1. Create conversation
    res = api_client.post("/api/conversations", json={"title": "REST Test Chat", "model": "gemini-2.5-pro"})
    assert res.status_code == 201
    conv_data = res.json()
    conv_id = conv_data["id"]
    assert conv_data["title"] == "REST Test Chat"

    # 2. List conversations
    list_res = api_client.get("/api/conversations?page=1&limit=10")
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["total"] >= 1
    assert any(c["id"] == conv_id for c in list_data["conversations"])

    # 3. Patch conversation
    patch_res = api_client.patch(f"/api/conversations/{conv_id}", json={"title": "Updated REST Title", "is_pinned": True})
    assert patch_res.status_code == 200
    assert patch_res.json()["title"] == "Updated REST Title"
    assert patch_res.json()["is_pinned"] is True

    # 4. Get conversation
    get_res = api_client.get(f"/api/conversations/{conv_id}")
    assert get_res.status_code == 200
    assert get_res.json()["conversation"]["title"] == "Updated REST Title"

    # 5. Delete conversation
    del_res = api_client.delete(f"/api/conversations/{conv_id}")
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True

    # Confirm 404 after deletion
    get_after = api_client.get(f"/api/conversations/{conv_id}")
    assert get_after.status_code == 404


def test_branching_and_export_endpoints(api_client: TestClient):
    # Create conversation
    res = api_client.post("/api/conversations", json={"title": "Branch & Export Test"})
    conv_id = res.json()["id"]

    # Fork root turn
    f1 = api_client.post(f"/api/conversations/{conv_id}/fork", json={"role": "user", "content": "Question 1"})
    assert f1.status_code == 200
    u1_id = f1.json()["created_message"]["id"]

    # Fork assistant turn
    f2 = api_client.post(f"/api/conversations/{conv_id}/fork", json={"parent_id": u1_id, "role": "assistant", "content": "Answer 1", "thoughts": "Reasoning 1"})
    assert f2.status_code == 200
    a1_id = f2.json()["created_message"]["id"]

    # Edit message U1 to create branch
    edit_res = api_client.post(f"/api/conversations/{conv_id}/messages/{u1_id}/edit", json={"content": "Question 1 Alternate"})
    assert edit_res.status_code == 200
    u1_alt_id = edit_res.json()["created_message"]["id"]

    # Switch branch back to A1
    branch_res = api_client.post(f"/api/conversations/{conv_id}/branch", json={"target_message_id": a1_id})
    assert branch_res.status_code == 200
    assert branch_res.json()["active_leaf_message_id"] == a1_id

    # Test Markdown Export
    exp_md = api_client.get(f"/api/conversations/{conv_id}/export?format=markdown")
    assert exp_md.status_code == 200
    assert exp_md.headers["content-type"].startswith("text/markdown")
    assert "### User" in exp_md.text
    assert "Thought Process" in exp_md.text

    # Test JSON Export
    exp_json = api_client.get(f"/api/conversations/{conv_id}/export?format=json")
    assert exp_json.status_code == 200
    assert exp_json.headers["content-type"].startswith("application/json")
    json_data = exp_json.json()
    assert json_data["$schema"]
    assert len(json_data["active_branch"]) == 2

    # Test Instant Search
    search_res = api_client.get("/api/search?q=Question")
    assert search_res.status_code == 200
    s_data = search_res.json()
    assert s_data["count"] >= 1


def test_fork_parent_validation_and_integrity_error_handling(api_client: TestClient):
    """
    Verifies that POST /api/conversations/{id}/fork:
    1. Successfully creates root turn when parent_id is None.
    2. Successfully creates child turn when parent_id is valid.
    3. Rejects nonexistent parent_id with 400 Bad Request and exact error detail.
    4. Rejects cross-conversation parent_id with 400 Bad Request and exact error detail.
    5. Rejects malformed parent_id (non-integer string) with 400 Bad Request.
    6. Rejects fork on nonexistent conversation with 404 Not Found.
    """
    # 1. Setup two conversations
    c1 = api_client.post("/api/conversations", json={"title": "Fork Test 1"}).json()["id"]
    c2 = api_client.post("/api/conversations", json={"title": "Fork Test 2"}).json()["id"]

    # 2. Valid root turn in c1
    r_root = api_client.post(f"/api/conversations/{c1}/fork", json={"role": "user", "content": "Root turn"})
    assert r_root.status_code == 200
    m1_id = r_root.json()["created_message"]["id"]

    # 3. Valid child turn in c1
    r_child = api_client.post(
        f"/api/conversations/{c1}/fork",
        json={"parent_id": m1_id, "role": "assistant", "content": "Child turn"},
    )
    assert r_child.status_code == 200

    # 4. Nonexistent parent_id rejection
    r_nonexistent = api_client.post(
        f"/api/conversations/{c1}/fork",
        json={"parent_id": 99999999, "role": "user", "content": "Orphan"},
    )
    assert r_nonexistent.status_code == 400
    assert r_nonexistent.json()["detail"] == "Invalid parent_id or parent does not belong to conversation"

    # 5. Cross-conversation parent_id rejection
    r_cross = api_client.post(
        f"/api/conversations/{c2}/fork",
        json={"parent_id": m1_id, "role": "user", "content": "Cross-conv parent"},
    )
    assert r_cross.status_code == 400
    assert r_cross.json()["detail"] == "Invalid parent_id or parent does not belong to conversation"

    # 6. Malformed parent_id rejection
    r_malformed = api_client.post(
        f"/api/conversations/{c1}/fork",
        json={"parent_id": "not_an_int", "role": "user", "content": "Malformed parent"},
    )
    assert r_malformed.status_code == 400
    assert r_malformed.json()["detail"] == "Invalid parent_id or parent does not belong to conversation"

    # 7. Nonexistent conversation rejection
    r_noconv = api_client.post(
        "/api/conversations/nonexistent_cid/fork",
        json={"role": "user", "content": "Orphan conversation"},
    )
    assert r_noconv.status_code == 404
    assert r_noconv.json()["detail"] == "Conversation not found"

