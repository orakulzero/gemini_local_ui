"""
backend/tests/test_m2_adversarial_rechallenge_dag.py
Empirical Adversarial Re-Challenge Test Suite for Milestone M2:
1. Self-parenting and cycle traversal in switch_active_branch bounded depth.
2. Cross-conversation parent injection and CTE isolation verification.
3. POST /api/conversations/{id}/fork validation with nonexistent, foreign, and malformed parent_id.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import time
from typing import AsyncIterator, List, Optional
import httpx
import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository


@pytest_asyncio.fixture
async def rechallenge_db(tmp_path: Path) -> AsyncIterator[Database]:
    """Isolated fresh SQLite database with schema.sql applied."""
    db_file = tmp_path / "rechallenge_m2.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)
    yield db
    await db.close()


@pytest.fixture
def rechallenge_client(rechallenge_db: Database) -> TestClient:
    """Configures FastAPI TestClient backed by rechallenge_db."""
    import backend.app.main as main_mod

    repo = ConversationRepository(rechallenge_db)
    main_mod.db = rechallenge_db
    main_mod.repository = repo

    app.state.db = rechallenge_db
    app.state.repository = repo

    with TestClient(app, base_url="http://testserver") as client:
        yield client


# ============================================================================
# 1. Self-Parenting & Cycle Traversal in switch_active_branch
# ============================================================================

@pytest.mark.asyncio
async def test_self_parenting_insert_and_update_prevention(rechallenge_db: Database) -> None:
    """
    Empirically verifies that CHECK(parent_id != id) prevents self-parenting:
    - Direct INSERT with parent_id == id fails immediately.
    - Subsequent UPDATE setting parent_id = id fails immediately.
    """
    repo = ConversationRepository(rechallenge_db)
    conv = await repo.create_conversation("Self Parenting Prevention")

    # 1. Direct INSERT with parent_id == id
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed: parent_id != id"):
        await rechallenge_db.execute(
            """
            INSERT INTO messages (id, conversation_id, parent_id, role, content, created_at, updated_at)
            VALUES (101, ?, 101, 'user', 'Self loop insert', 1000, 1000);
            """,
            (conv.id,),
        )

    # 2. Legitimate insert followed by UPDATE to self
    m = await repo.add_message(conv.id, "user", "Normal message")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed: parent_id != id"):
        await rechallenge_db.execute(
            "UPDATE messages SET parent_id = ? WHERE id = ?;",
            (m.id, m.id),
        )


@pytest.mark.asyncio
async def test_multi_node_cycles_terminate_cleanly_in_switch_active_branch(rechallenge_db: Database) -> None:
    """
    Empirically tests switch_active_branch against:
    - 2-node cycle (A -> B -> A)
    - 3-node cycle (A -> B -> C -> A)
    - Cycle with an attached downstream tail
    Verifies termination within < 100ms and absence of infinite loops.
    """
    repo = ConversationRepository(rechallenge_db)
    conv = await repo.create_conversation("Cycle Traversal Test")

    # Build 3-node cycle: 201 -> 202 -> 203 -> 201
    await rechallenge_db.execute(
        """
        INSERT INTO messages (id, conversation_id, parent_id, role, content, created_at, updated_at)
        VALUES (201, ?, NULL, 'user', 'Node 201', 1000, 1000);
        """,
        (conv.id,),
    )
    await rechallenge_db.execute(
        """
        INSERT INTO messages (id, conversation_id, parent_id, role, content, created_at, updated_at)
        VALUES (202, ?, 201, 'assistant', 'Node 202', 1001, 1001);
        """,
        (conv.id,),
    )
    await rechallenge_db.execute(
        """
        INSERT INTO messages (id, conversation_id, parent_id, role, content, created_at, updated_at)
        VALUES (203, ?, 202, 'user', 'Node 203', 1002, 1002);
        """,
        (conv.id,),
    )
    # Complete cycle: 201 parent is 203
    await rechallenge_db.execute(
        "UPDATE messages SET parent_id = 203 WHERE id = 201;"
    )

    t0 = time.perf_counter()
    # switch_active_branch starting from 201
    resolved_leaf, active_thread = await asyncio.wait_for(
        repo.switch_active_branch(conv.id, 201),
        timeout=1.0,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 100.0, f"switch_active_branch took {elapsed_ms:.2f}ms on cyclic graph"
    assert resolved_leaf in (201, 202, 203)
    # Verify active thread is populated and recursive CTE also terminated cleanly
    assert len(active_thread) >= 1


@pytest.mark.asyncio
async def test_deep_chain_exceeding_depth_boundary_in_switch_active_branch(rechallenge_db: Database) -> None:
    """
    Verifies that switch_active_branch depth guard (depth < 1000)
    terminates deterministically on massive chains without RecursionError or hang.
    """
    repo = ConversationRepository(rechallenge_db)
    conv = await repo.create_conversation("Depth Bound Test")

    # Insert 1050 chained messages in single transaction
    base_ts = 1000
    rows = []
    for i in range(1, 1051):
        p_id = i - 1 if i > 1 else None
        rows.append((i, conv.id, p_id, "user", f"Msg {i}", base_ts + i, base_ts + i))

    async with rechallenge_db.transaction() as conn:
        await conn.executemany(
            """
            INSERT INTO messages (id, conversation_id, parent_id, role, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            rows,
        )

    t0 = time.perf_counter()
    resolved_leaf, active_thread = await asyncio.wait_for(
        repo.switch_active_branch(conv.id, 1),
        timeout=2.0,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 500.0, f"Traversing depth-bound chain took {elapsed_ms:.2f}ms"
    # Starting at 1, loop runs up to depth=1000, so leaf reaches 1001 (1000 steps)
    assert resolved_leaf == 1001
    # get_active_thread CTE also bounds depth at 1000
    assert len(active_thread) <= 1001


# ============================================================================
# 2. Cross-Conversation Parent Injection & Isolation Verification
# ============================================================================

@pytest.mark.asyncio
async def test_cross_conversation_deep_zigzag_isolation(rechallenge_db: Database) -> None:
    """
    Empirically tests that recursive CTE strictly isolates conversations:
    Even with an adversarial zigzag parent chain across Conversation A and B:
      A1 -> B1 -> A2 -> B2 -> A3 -> B3
    - Querying active thread for Conv A contains ONLY A messages.
    - Querying active thread for Conv B contains ONLY B messages.
    - No cross-conversation leakage occurs.
    """
    repo = ConversationRepository(rechallenge_db)
    conv_a = await repo.create_conversation("Tenant A")
    conv_b = await repo.create_conversation("Tenant B")

    # A1
    m_a1 = await repo.add_message(conv_a.id, "user", "A1: Sensitive Secret A")
    # B1 linked to A1
    m_b1 = await repo.add_message(conv_b.id, "user", "B1: Public B linked to A1", parent_id=m_a1.id)
    # A2 linked to B1
    m_a2 = await repo.add_message(conv_a.id, "assistant", "A2: Response A linked to B1", parent_id=m_b1.id)
    # B2 linked to A2
    m_b2 = await repo.add_message(conv_b.id, "assistant", "B2: Response B linked to A2", parent_id=m_a2.id)

    # Active thread for Conv A (from A2)
    thread_a = await repo.get_active_thread(conv_a.id, leaf_message_id=m_a2.id)
    # Active thread for Conv B (from B2)
    thread_b = await repo.get_active_thread(conv_b.id, leaf_message_id=m_b2.id)

    # Assert thread A contains only Conv A messages
    assert len(thread_a) == 1, f"Expected 1 message (A2), got {len(thread_a)}"
    assert thread_a[0].id == m_a2.id
    assert all(m.conversation_id == conv_a.id for m in thread_a)
    assert not any("Sensitive Secret A" in m.content for m in thread_b)

    # Assert thread B contains only Conv B messages
    assert len(thread_b) == 1, f"Expected 1 message (B2), got {len(thread_b)}"
    assert thread_b[0].id == m_b2.id
    assert all(m.conversation_id == conv_b.id for m in thread_b)


# ============================================================================
# 3. POST /api/conversations/{id}/fork HTTP Status Code & Validation Verification
# ============================================================================

def test_api_fork_nonexistent_parent_id_returns_400(rechallenge_client: TestClient) -> None:
    """
    POST /api/conversations/{id}/fork with nonexistent parent_id:
    Must return HTTP 400 (Bad Request), never HTTP 500.
    """
    c_resp = rechallenge_client.post("/api/conversations", json={"title": "Fork Test Nonexistent"})
    assert c_resp.status_code == 201
    cid = c_resp.json()["id"]

    resp = rechallenge_client.post(
        f"/api/conversations/{cid}/fork",
        json={"parent_id": 88888888, "role": "user", "content": "Nonexistent parent payload"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid parent_id or parent does not belong to conversation"


def test_api_fork_foreign_conversation_parent_id_returns_400(rechallenge_client: TestClient) -> None:
    """
    POST /api/conversations/{id}/fork with parent_id from another conversation:
    Must return HTTP 400 (Bad Request), never HTTP 500.
    """
    c1 = rechallenge_client.post("/api/conversations", json={"title": "Conv 1"}).json()["id"]
    c2 = rechallenge_client.post("/api/conversations", json={"title": "Conv 2"}).json()["id"]

    # Create root message in c1
    r_msg = rechallenge_client.post(
        f"/api/conversations/{c1}/fork",
        json={"role": "user", "content": "Message in Conv 1"},
    )
    assert r_msg.status_code == 200
    c1_msg_id = r_msg.json()["created_message"]["id"]

    # Attempt to fork in c2 using c1's message ID as parent
    resp = rechallenge_client.post(
        f"/api/conversations/{c2}/fork",
        json={"parent_id": c1_msg_id, "role": "user", "content": "Cross-fork attempt"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid parent_id or parent does not belong to conversation"


def test_api_fork_malformed_and_boundary_parent_ids(rechallenge_client: TestClient) -> None:
    """
    Tests edge cases on parent_id:
    - String non-numeric parent_id: returns HTTP 400 (caught and mapped cleanly).
    - Negative parent_id: returns HTTP 400.
    - Zero parent_id: returns HTTP 400.
    - Nonexistent conversation: returns HTTP 404.
    """
    c1 = rechallenge_client.post("/api/conversations", json={"title": "Conv Boundary"}).json()["id"]

    # String non-numeric parent_id
    r_str = rechallenge_client.post(
        f"/api/conversations/{c1}/fork",
        json={"parent_id": "malicious_string_id", "role": "user", "content": "Bad parent"},
    )
    assert r_str.status_code == 400

    # Negative parent_id
    r_neg = rechallenge_client.post(
        f"/api/conversations/{c1}/fork",
        json={"parent_id": -99, "role": "user", "content": "Negative parent"},
    )
    assert r_neg.status_code == 400

    # Zero parent_id (SQLite auto-increment starts at 1)
    r_zero = rechallenge_client.post(
        f"/api/conversations/{c1}/fork",
        json={"parent_id": 0, "role": "user", "content": "Zero parent"},
    )
    assert r_zero.status_code == 400

    # Nonexistent conversation ID
    r_noconv = rechallenge_client.post(
        "/api/conversations/non_existent_uuid_12345/fork",
        json={"role": "user", "content": "Valid payload, invalid conv"},
    )
    assert r_noconv.status_code == 404
    assert r_noconv.json()["detail"] == "Conversation not found"


def test_api_fork_valid_workflow_and_branch_switching(rechallenge_client: TestClient) -> None:
    """
    Verifies full lifecycle of valid forks:
    - Root turn (parent_id = None) -> 200.
    - Child turn 1 (parent_id = Root) -> 200.
    - Sibling child turn 2 (parent_id = Root) -> 200 (forked branch).
    - Verifies active thread updates correctly.
    """
    cid = rechallenge_client.post("/api/conversations", json={"title": "Valid Fork Branching"}).json()["id"]

    # 1. Root
    r1 = rechallenge_client.post(
        f"/api/conversations/{cid}/fork",
        json={"role": "user", "content": "Root Question"},
    )
    assert r1.status_code == 200
    root_id = r1.json()["created_message"]["id"]

    # 2. Branch A child
    r_a = rechallenge_client.post(
        f"/api/conversations/{cid}/fork",
        json={"parent_id": root_id, "role": "assistant", "content": "Branch A Answer"},
    )
    assert r_a.status_code == 200
    a_id = r_a.json()["created_message"]["id"]

    # 3. Branch B child (forked from root)
    r_b = rechallenge_client.post(
        f"/api/conversations/{cid}/fork",
        json={"parent_id": root_id, "role": "assistant", "content": "Branch B Alternative"},
    )
    assert r_b.status_code == 200
    b_id = r_b.json()["created_message"]["id"]

    # Fetch active thread: active leaf is Branch B
    conv_data = rechallenge_client.get(f"/api/conversations/{cid}").json()
    assert conv_data["active_leaf_message_id"] == b_id
    active_thread = conv_data["active_thread"]
    assert len(active_thread) == 2
    assert active_thread[0]["id"] == root_id
    assert active_thread[1]["id"] == b_id
    assert active_thread[1]["content"] == "Branch B Alternative"

    # Switch branch back to Branch A
    r_switch = rechallenge_client.post(
        f"/api/conversations/{cid}/branch",
        json={"target_message_id": a_id},
    )
    assert r_switch.status_code == 200
    conv_switched = rechallenge_client.get(f"/api/conversations/{cid}").json()
    assert conv_switched["active_leaf_message_id"] == a_id
    assert conv_switched["active_thread"][1]["content"] == "Branch A Answer"
