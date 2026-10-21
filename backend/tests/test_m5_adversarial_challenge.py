"""
backend/tests/test_m5_adversarial_challenge.py
Adversarial challenge and stress-test suite for Milestone M5:
- Wire Protocol: inner[45]=1 strictly at index 45 of 81-element envelope across permutations
- Persistence Integrity: Zero SQLite rows (conversations, messages, attachments, FTS) across multi-turn incognito
- Error 1050 Prevention: Multi-model regeneration (4 Argon, 3.1 Pro, 3.8 Flash, 3.5 Flash-Lite) DAG branching & wire cid omission
- Cloud Deletion: GzXR5e RPC invocation when remote=true, bypass when remote=false, and fault handling
- Boundary PATCH: Empty title, unicode characters, SQL injection injection resistance, rapid pin toggles
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List
import pytest
from starlette.testclient import TestClient

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import DEFAULT_MODELS, resolve_model
from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine

MOCK_PORT = 9245
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
def m5_adv_context(tmp_path: Path, mock_server: MockGeminiServer):
    db_file = tmp_path / "test_m5_adv.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    asyncio.run(db.init_schema(schema_path))

    repo = ConversationRepository(db)
    search_eng = SearchEngine(db)

    sm = SessionManager(base_url=MOCK_BASE_URL)
    sm.context.cookies = {
        "__Secure-1PSID": "adv_test_psid_token_12345",
        "__Secure-1PSIDTS": "adv_test_psidts_token_67890",
    }
    sm.context.access_token = "adv_test_token_abc"

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
    yield client, repo, db, sm, mock_server
    asyncio.run(db.close())


def parse_sse_events(response_text: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for line in response_text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return events


# ============================================================================
# VECTOR 1: Envelope Index 45 Strictness & Structural Invariance
# ============================================================================

class TestEnvelopeWireStrictness:
    """Stress-test Google's 81-element envelope under combinatorial variations."""

    @pytest.mark.parametrize("incognito_val,expected_at_45", [
        (True, 1),
        (False, None),
    ])
    def test_strict_envelope_length_and_index_45(self, incognito_val: bool, expected_at_45: Any):
        client = GeminiWebClient(base_url=MOCK_BASE_URL)
        envelope = client._build_81_element_envelope(
            prompt="Hello world",
            incognito=incognito_val,
        )
        # 1. Total length must be strictly 81
        assert len(envelope) == 81, f"Envelope length must be strictly 81, got {len(envelope)}"

        # 2. Index 45 must strictly match incognito setting
        assert envelope[45] == expected_at_45, f"inner[45] expected {expected_at_45}, got {envelope[45]}"

        # 3. Off-by-one verification: neighbours 44 and 46 must NOT be affected
        assert envelope[44] is None or envelope[44] == 0, f"inner[44] corrupted: {envelope[44]}"
        assert envelope[46] is None or envelope[46] == 0, f"inner[46] corrupted: {envelope[46]}"

    def test_envelope_combinatorial_permutations_preserve_index_45(self):
        """Verify that varying attachments, models, feature_modes, and tags never shifts index 45."""
        client = GeminiWebClient(base_url=MOCK_BASE_URL)

        variations = [
            # prompt, attachments, model_num, thinking, feat_mode, mode_tag
            ("", None, 1, 1, 0, None),
            ("A" * 10000, [{"uri": "http://img.jpg", "filename": "test.jpg"}], 1, 2, 0, None),
            ("Complex prompt", [("http://file1", "f1"), ("http://file2", "f2")], 2, 4, 40, "Argon4p0"),
            ("System injection", None, 4, 1, 40, "SparkTask"),
        ]

        for p, atts, m_num, think, f_mode, m_tag in variations:
            for incog in [True, False]:
                env = client._build_81_element_envelope(
                    prompt=p,
                    attachments=atts,
                    model_number=m_num,
                    thinking_flag=think,
                    feature_mode=f_mode,
                    mode_tag=m_tag,
                    incognito=incog,
                )
                assert len(env) == 81, f"Length broke under variation: {len(env)}"
                assert env[45] == (1 if incog else None), f"Index 45 shifted under variation!"
                assert env[79] == m_num, "Model number shifted from index 79"
                assert env[80] == think, "Thinking flag shifted from index 80"
                if f_mode == 40:
                    assert env[49] == 40, "Task feature mode shifted from index 49"
                if m_tag:
                    assert env[64] == m_tag, "Mode tag shifted from index 64"


# ============================================================================
# VECTOR 2: Zero SQLite Persistence Across Multi-Turn Incognito
# ============================================================================

class TestIncognitoZeroPersistence:
    """Stress-test that multi-turn incognito chats leave zero footprint in SQLite."""

    def test_multi_turn_incognito_leaves_zero_rows(self, m5_adv_context):
        client, repo, db, _, _ = m5_adv_context
        conv_id = "incognito_session_alpha_1"

        turns = [
            {"prompt": "Turn 1: Secret question about nuclear physics"},
            {"prompt": "Turn 2: What are the isotopes?", "system_instruction": "You are a physicist"},
            {"prompt": "Turn 3: Uploading formula", "attachments": [{"filename": "doc.pdf", "uri": "https://storage.google.com/doc.pdf"}]},
            {"prompt": "Turn 4: Continued conversation", "parent_message_id": 999999},
            {"prompt": "Turn 5: Final confidential query"},
        ]

        for i, turn in enumerate(turns):
            payload = {
                "conversation_id": conv_id,
                "model": "56fdd199312815e2",
                "incognito": True,
                **turn,
            }
            res = client.post("/api/chat/generate", json=payload)
            assert res.status_code == 200, f"Turn {i} failed: {res.text}"

            events = parse_sse_events(res.text)
            start_ev = next((e for e in events if e.get("type") == "start"), None)
            assert start_ev is not None, f"Turn {i} missing start event"
            ephemeral_user_id = start_ev.get("user_message_id")
            assert ephemeral_user_id is not None

            saved_ev = next((e for e in events if e.get("type") == "saved"), None)
            assert saved_ev is not None, f"Turn {i} missing saved event"
            # Assistant message ID MUST be None
            assert saved_ev.get("assistant_message_id") is None, f"Turn {i} returned non-null assistant_id!"

            # Ephemeral user ID must NOT exist in repository
            async def verify_ephemeral_absent():
                m = await repo.get_message(ephemeral_user_id)
                assert m is None, f"Ephemeral user message {ephemeral_user_id} was persisted!"

            asyncio.run(verify_ephemeral_absent())

        # Final verification: Database must be 100% clean across all relevant tables
        async def assert_total_db_cleanliness():
            # 1. Zero conversations
            conv_cnt = await db.fetch_val("SELECT COUNT(*) FROM conversations;")
            assert conv_cnt == 0, f"Found {conv_cnt} conversations after incognito turns!"

            # 2. Zero messages
            msg_cnt = await db.fetch_val("SELECT COUNT(*) FROM messages;")
            assert msg_cnt == 0, f"Found {msg_cnt} messages after incognito turns!"

            # 3. Zero attachments
            att_cnt = await db.fetch_val("SELECT COUNT(*) FROM attachments;")
            assert att_cnt == 0, f"Found {att_cnt} attachments after incognito turns!"

            # 4. Zero FTS entries
            fts_cnt = await db.fetch_val("SELECT COUNT(*) FROM messages_fts;")
            assert fts_cnt == 0, f"Found {fts_cnt} FTS rows after incognito turns!"

            # 5. Repository list API returns 0
            convs, total = await repo.list_conversations()
            assert total == 0
            assert len(convs) == 0

        asyncio.run(assert_total_db_cleanliness())

        # UI endpoint GET /api/conversations must also return empty
        res_list = client.get("/api/conversations")
        assert res_list.status_code == 200
        assert res_list.json()["total"] == 0
        assert len(res_list.json()["conversations"]) == 0


# ============================================================================
# VECTOR 3: Error 1050 Prevention & Multi-Model Mid-Chat DAG Branching
# ============================================================================

class TestError1050PreventionAndDAGBranching:
    """
    Stress-test cross-model regeneration across 4 Argon, 3.1 Pro, 3.8 Flash, 3.5 Flash-Lite.
    Empirically verifies:
    1. Wire: No stale Google cid is sent on cross-model regeneration (preventing Error 1050).
    2. DAG: Local messages branch under identical parent user turn.
    """

    def test_multi_model_regeneration_dag_tree(self, m5_adv_context):
        client, repo, db, _, mock_server = m5_adv_context

        # 1. Create base conversation
        c_res = client.post("/api/conversations", json={
            "title": "Quantum Computing Deep Dive",
            "model": "56fdd199312815e2",  # Flash
        })
        assert c_res.status_code == 201
        conv_id = c_res.json()["id"]

        # 2. Seed initial User turn + Assistant turn 1 (Flash) with a remote_conversation_id
        async def seed_chat():
            u = await repo.add_message(
                conversation_id=conv_id,
                role="user",
                content="Explain quantum entanglement rigorously.",
                model="56fdd199312815e2",
            )
            a1 = await repo.add_message(
                conversation_id=conv_id,
                role="assistant",
                content="Flash: Entanglement is non-local correlation.",
                parent_id=u.id,
                model="56fdd199312815e2",
            )
            await repo.update_conversation(
                conv_id,
                active_leaf_message_id=a1.id,
                remote_conversation_id="google_cid_stale_flash_session",
            )
            return u.id, a1.id

        user_msg_id, a1_id = asyncio.run(seed_chat())

        # Models to regenerate sequentially from user_msg_id
        target_models = [
            ("95a4c0abcba24364@thinking=4@mode=chat", "4 Argon"),
            ("e6fa609c3fa255c0", "3.1 Pro"),
            ("56fdd199312815e2", "3.8 Flash"),
            ("cf41b0e0dd7d53e5", "3.5 Flash-Lite"),
        ]

        regenerated_assistant_ids: List[int] = [a1_id]

        for model_id, model_name in target_models:
            req_idx_before = len(mock_server.fault.recorded_requests)

            res = client.post("/api/chat/generate", json={
                "conversation_id": conv_id,
                "existing_user_message_id": user_msg_id,
                "model": model_id,
                "prompt": "Explain quantum entanglement rigorously.",
            })
            assert res.status_code == 200, f"Regeneration with {model_name} failed: {res.text}"
            events = parse_sse_events(res.text)

            saved_ev = next((e for e in events if e.get("type") == "saved"), None)
            assert saved_ev is not None
            new_asst_id = saved_ev.get("assistant_message_id")
            assert new_asst_id is not None
            assert new_asst_id not in regenerated_assistant_ids
            regenerated_assistant_ids.append(new_asst_id)

            # Wire verification: Check that StreamGenerate request did NOT pass the stale cid
            stream_reqs = [
                r for r in mock_server.fault.recorded_requests[req_idx_before:]
                if "StreamGenerate" in r.get("path", "")
            ]
            assert len(stream_reqs) >= 1
            wire_req = stream_reqs[0]
            f_req = wire_req["body_params"].get("f.req", "")
            envelope = json.loads(f_req)
            inner_str = envelope[1]
            inner_json = json.loads(inner_str)
            cid_element = inner_json[2]
            # inner[2] is [cid, rid, rcid] -> cid must NOT be 'google_cid_stale_flash_session'
            assert cid_element[0] != "google_cid_stale_flash_session", (
                f"Error 1050 risk! Stale cid was sent over wire during {model_name} regeneration!"
            )
            assert cid_element[0] == "", f"Expected empty cid for fresh remote turn, got: {cid_element[0]}"

        # 3. Local DAG Tree Structure Verification
        async def verify_dag_branches():
            all_msgs = await repo.get_all_messages(conv_id)
            # 1 user message + 1 initial assistant + 4 regenerated = 6 messages
            assert len(all_msgs) == 6

            user_msg = await repo.get_message(user_msg_id)
            assert user_msg is not None

            # All 5 assistant messages must share user_msg_id as parent_id
            asst_msgs = [m for m in all_msgs if m.role == "assistant"]
            assert len(asst_msgs) == 5
            for m in asst_msgs:
                assert m.parent_id == user_msg_id, f"Message {m.id} parent_id is {m.parent_id}, expected {user_msg_id}"

            # Verify that threads reconstruct properly for any leaf
            for asst_id in regenerated_assistant_ids:
                thread = await repo.get_active_thread(conv_id, leaf_message_id=asst_id)
                assert len(thread) == 2
                assert thread[0].id == user_msg_id
                assert thread[1].id == asst_id

        asyncio.run(verify_dag_branches())


# ============================================================================
# VECTOR 4: GzXR5e Cloud Deletion Wire Invocation & Fault Resilience
# ============================================================================

class TestCloudDeletionGzXR5e:
    """Stress-test GzXR5e remote deletion vs local-only deletion and failure modes."""

    def test_remote_true_invokes_gzxr5e_with_exact_cid(self, m5_adv_context):
        client, repo, _, _, mock_server = m5_adv_context
        test_cid = "google_cloud_cid_omega_777"

        # Create conversation with linked remote_conversation_id
        r = client.post("/api/conversations", json={"title": "Cloud Deletion Target", "model": "56fdd199312815e2"})
        c_id = r.json()["id"]

        async def attach_remote():
            await repo.update_conversation(c_id, remote_conversation_id=test_cid)
        asyncio.run(attach_remote())

        req_count_before = len(mock_server.fault.recorded_requests)

        # Execute DELETE with remote=true
        del_res = client.delete(f"/api/conversations/{c_id}?remote=true")
        assert del_res.status_code == 200
        data = del_res.json()
        assert data["success"] is True
        assert data["remote_deleted"] is True

        # Verify wire call to mock server
        new_reqs = mock_server.fault.recorded_requests[req_count_before:]
        gzxr5e_reqs = [
            req for req in new_reqs
            if "batchexecute" in req.get("path", "") and "GzXR5e" in req.get("params", {}).get("rpcids", "")
        ]
        assert len(gzxr5e_reqs) == 1, "GzXR5e RPC was not invoked on Google mock server!"

        # Inspect wire body payload
        f_req = gzxr5e_reqs[0]["body_params"].get("f.req", "")
        envelope = json.loads(f_req)
        # envelope is [[[rpc_id, escaped_payload, None, "generic"]]]
        entry = envelope[0][0]
        assert entry[0] == "GzXR5e"
        inner_payload = json.loads(entry[1])
        assert inner_payload == [test_cid], f"GzXR5e payload expected {[test_cid]}, got {inner_payload}"

    def test_remote_false_bypasses_gzxr5e(self, m5_adv_context):
        client, repo, _, _, mock_server = m5_adv_context
        test_cid = "google_cloud_cid_do_not_delete"

        r = client.post("/api/conversations", json={"title": "Local Only Deletion Target", "model": "56fdd199312815e2"})
        c_id = r.json()["id"]

        async def attach_remote():
            await repo.update_conversation(c_id, remote_conversation_id=test_cid)
        asyncio.run(attach_remote())

        req_count_before = len(mock_server.fault.recorded_requests)

        # Execute DELETE with remote=false
        del_res = client.delete(f"/api/conversations/{c_id}?remote=false")
        assert del_res.status_code == 200
        data = del_res.json()
        assert data["success"] is True
        assert data["remote_deleted"] is False

        # Verify NO GzXR5e call was made
        new_reqs = mock_server.fault.recorded_requests[req_count_before:]
        gzxr5e_reqs = [
            req for req in new_reqs
            if "batchexecute" in req.get("path", "") and "GzXR5e" in req.get("params", {}).get("rpcids", "")
        ]
        assert len(gzxr5e_reqs) == 0, f"GzXR5e should have been bypassed when remote=false!"

    def test_remote_true_on_local_only_chat_skips_rpc(self, m5_adv_context):
        """When conversation has no remote_conversation_id, remote=true skips RPC without error."""
        client, repo, _, _, mock_server = m5_adv_context

        r = client.post("/api/conversations", json={"title": "Local Only Chat", "model": "56fdd199312815e2"})
        c_id = r.json()["id"]

        req_count_before = len(mock_server.fault.recorded_requests)
        del_res = client.delete(f"/api/conversations/{c_id}?remote=true")
        assert del_res.status_code == 200
        assert del_res.json()["remote_deleted"] is False

        new_reqs = mock_server.fault.recorded_requests[req_count_before:]
        gzxr5e_reqs = [
            req for req in new_reqs
            if "batchexecute" in req.get("path", "") and "GzXR5e" in req.get("params", {}).get("rpcids", "")
        ]
        assert len(gzxr5e_reqs) == 0

    def test_remote_failure_still_deletes_locally(self, m5_adv_context, monkeypatch):
        """Even if remote GzXR5e raises a network exception, local deletion completes cleanly."""
        client, repo, _, _, _ = m5_adv_context

        r = client.post("/api/conversations", json={"title": "Faulty Remote Chat", "model": "56fdd199312815e2"})
        c_id = r.json()["id"]

        async def attach_remote():
            await repo.update_conversation(c_id, remote_conversation_id="remote_error_cid")
        asyncio.run(attach_remote())

        # Simulate GeminiWebClient failure
        async def mock_fail_delete(self, cid):
            raise RuntimeError("Google upstream network timeout (504)")

        monkeypatch.setattr(GeminiWebClient, "delete_conversation", mock_fail_delete)

        del_res = client.delete(f"/api/conversations/{c_id}?remote=true")
        assert del_res.status_code == 200
        data = del_res.json()
        assert data["success"] is True
        assert data["remote_deleted"] is False

        # Confirm locally deleted
        get_res = client.get(f"/api/conversations/{c_id}")
        assert get_res.status_code == 404


# ============================================================================
# VECTOR 5: Boundary Testing for PATCH /api/conversations/{id}
# ============================================================================

class TestConversationPatchBoundaries:
    """Boundary and stress tests for PATCH /api/conversations/{id}."""

    def test_patch_empty_title_and_special_unicode(self, m5_adv_context):
        client, repo, _, _, _ = m5_adv_context

        r = client.post("/api/conversations", json={"title": "Original Title", "model": "56fdd199312815e2"})
        c_id = r.json()["id"]

        # 1. Empty string title
        p1 = client.patch(f"/api/conversations/{c_id}", json={"title": ""})
        assert p1.status_code == 200
        assert p1.json()["title"] == ""

        # Export should fall back gracefully to "conversation.md"
        exp = client.get(f"/api/conversations/{c_id}/export?format=markdown")
        assert exp.status_code == 200
        assert "conversation.md" in exp.headers["content-disposition"]

        # 2. Rich Unicode strings: Emojis, Cyrillic, CJK, Math, RTL, File System delimiters
        unicode_titles = [
            "🤖 🔥 🚀 🧠 💎 🪐",
            "Test heading in English with special characters: !@#$%^&*()_+",
            "日本語タイトルと中文测试和한국어",
            "∀x ∈ ℝ: x² ≥ 0 → ∇ × E = -∂B/∂t",
            'Illegal chars: / \\ : * ? " < > |',
            "Zero-width and RTL: \u200b\u200e\u202eTEST_RTL\u202c",
            "Long title: " + ("ABC " * 100),
        ]

        for u_title in unicode_titles:
            p_res = client.patch(f"/api/conversations/{c_id}", json={"title": u_title})
            assert p_res.status_code == 200
            assert p_res.json()["title"] == u_title

            get_res = client.get(f"/api/conversations/{c_id}")
            assert get_res.status_code == 200
            assert get_res.json()["conversation"]["title"] == u_title

    def test_patch_sql_injection_resilience(self, m5_adv_context):
        """Verify title is strictly parameterized against SQL injection attempts."""
        client, _, db, _, _ = m5_adv_context

        r = client.post("/api/conversations", json={"title": "Safe Chat", "model": "56fdd199312815e2"})
        c_id = r.json()["id"]

        sql_injection_payloads = [
            "'; DROP TABLE conversations; --",
            "' OR '1'='1",
            "\" OR \"\"=\"",
            "'; UPDATE conversations SET is_pinned=1; --",
        ]

        for inj in sql_injection_payloads:
            p_res = client.patch(f"/api/conversations/{c_id}", json={"title": inj})
            assert p_res.status_code == 200
            assert p_res.json()["title"] == inj

        # Confirm conversations table is intact and has 1 conversation
        async def verify_table():
            cnt = await db.fetch_val("SELECT COUNT(*) FROM conversations;")
            assert cnt == 1

        asyncio.run(verify_table())

    def test_rapid_pin_toggles_and_ordering(self, m5_adv_context):
        """Perform 30 rapid pin toggles and verify ordering consistency."""
        client, repo, _, _, _ = m5_adv_context

        # Create 3 chats: C1, C2, C3
        c1 = client.post("/api/conversations", json={"title": "Chat 1"}).json()["id"]
        c2 = client.post("/api/conversations", json={"title": "Chat 2"}).json()["id"]
        c3 = client.post("/api/conversations", json={"title": "Chat 3"}).json()["id"]

        # Rapidly toggle is_pinned on C1 30 times
        current_state = False
        for i in range(30):
            current_state = not current_state
            res = client.patch(f"/api/conversations/{c1}", json={"is_pinned": current_state})
            assert res.status_code == 200
            assert res.json()["is_pinned"] == current_state

            # When pinned, C1 must be at the very top of listing
            list_res = client.get("/api/conversations").json()["conversations"]
            if current_state:
                assert list_res[0]["id"] == c1, f"Iteration {i}: C1 was pinned but not first in list!"
            else:
                # When unpinned, C3 or C1 based on updated_at
                assert any(c["id"] == c1 for c in list_res)

        # Final state check: unpin C1, pin C2
        client.patch(f"/api/conversations/{c1}", json={"is_pinned": False})
        client.patch(f"/api/conversations/{c2}", json={"is_pinned": True})

        final_list = client.get("/api/conversations").json()["conversations"]
        assert final_list[0]["id"] == c2
        assert final_list[0]["is_pinned"] is True

    def test_patch_non_existent_conversation(self, m5_adv_context):
        client, _, _, _, _ = m5_adv_context
        res = client.patch("/api/conversations/non_existent_id_9999", json={"title": "Ghost"})
        assert res.status_code == 404
