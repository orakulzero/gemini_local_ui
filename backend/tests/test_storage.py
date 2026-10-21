"""
backend/tests/test_storage.py
Automated test suite and performance benchmark for SQLite persistence,
DAG branching, FTS5 full-text search, query sanitization, thread export, and sub-50ms latency SLAs.
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import AsyncIterator, List
import pytest
import pytest_asyncio

from backend.app.storage.database import Database
from backend.app.storage.exporter import ThreadExporter
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine, sanitize_fts5_query


@pytest_asyncio.fixture
async def test_db(tmp_path: Path) -> AsyncIterator[Database]:
    """Provides a fresh temporary SQLite database initialized with schema.sql."""
    db_file = tmp_path / "test_gemini.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_database_crud_and_foreign_keys(test_db: Database) -> None:
    """Verifies relational CRUD operations and cascade deletions."""
    repo = ConversationRepository(test_db)
    now = int(time.time() * 1000)

    # 1. Create conversation
    conv = await repo.create_conversation(
        title="Test Conversation",
        model="gemini-2.5-pro",
        conversation_id="c_test_01",
    )
    assert conv.id == "c_test_01"
    assert conv.title == "Test Conversation"

    # 2. Insert message
    msg = await repo.add_message(
        conversation_id="c_test_01",
        role="user",
        content="Quantum computing question",
        thoughts="thinking trace",
        created_at=now,
    )
    assert msg.id is not None
    assert msg.role == "user"

    # 3. Insert attachment
    att = await repo.add_attachment(
        message_id=msg.id,
        filename="diagram.png",
        mime_type="image/png",
        size_bytes=4096,
        uri="file:///diagram.png",
    )
    assert att.message_id == msg.id

    # 4. Cascade delete conversation
    deleted = await repo.delete_conversation("c_test_01")
    assert deleted is True

    # Assert messages and attachments were cascaded
    msgs = await test_db.fetch_all("SELECT * FROM messages WHERE conversation_id = ?;", ("c_test_01",))
    assert len(msgs) == 0
    atts = await test_db.fetch_all("SELECT * FROM attachments WHERE id = ?;", (att.id,))
    assert len(atts) == 0


@pytest.mark.asyncio
async def test_query_sanitizer_edge_cases() -> None:
    """Verifies query sanitizer handles all syntax pitfalls safely."""
    assert sanitize_fts5_query("") == ""
    assert sanitize_fts5_query("   ") == ""
    assert sanitize_fts5_query("***") == ""
    assert sanitize_fts5_query("()") == ""
    assert sanitize_fts5_query("C++") == '"C"'
    assert sanitize_fts5_query("C++20") == '"C" "20"*'
    assert sanitize_fts5_query("Error: NullPointerException") == '"Error" "NullPointerException"*'
    assert sanitize_fts5_query('"unclosed quote') == '"unclosed" "quote"*'
    assert sanitize_fts5_query("AND OR NOT") == '"AND" "OR" "NOT"*'
    assert sanitize_fts5_query("quantum*") == '"quantum"*'
    assert sanitize_fts5_query("Hello") == '"Hello"*'


@pytest.mark.asyncio
async def test_fts5_triggers_synchronization(test_db: Database) -> None:
    """Verifies FTS5 index automatically updates on message INSERT, UPDATE, and DELETE."""
    search_engine = SearchEngine(test_db)
    repo = ConversationRepository(test_db)

    conv = await repo.create_conversation(title="FTS Sync Test", conversation_id="c_sync_01")

    # 1. Insert message
    msg = await repo.add_message(
        conversation_id=conv.id,
        role="assistant",
        content="Initial text about Shor algorithm.",
        thoughts="Factoring numbers.",
    )

    # Search should find it immediately
    results = await search_engine.search("Shor")
    assert len(results) == 1
    assert results[0].message_id == msg.id
    assert results[0].matched_field == "content"
    assert "<mark" in results[0].snippet

    # Search in thoughts
    thought_results = await search_engine.search("Factoring")
    assert len(thought_results) == 1
    assert thought_results[0].message_id == msg.id
    assert thought_results[0].matched_field == "thoughts"

    # 2. Update message content
    await test_db.execute(
        "UPDATE messages SET content = ? WHERE id = ?;",
        ("Updated text about Grover search algorithm.", msg.id),
    )

    # Old term should no longer match
    old_results = await search_engine.search("Shor")
    assert len(old_results) == 0

    # New term should match
    new_results = await search_engine.search("Grover")
    assert len(new_results) == 1
    assert new_results[0].message_id == msg.id

    # 3. Delete message
    await test_db.execute("DELETE FROM messages WHERE id = ?;", (msg.id,))
    del_results = await search_engine.search("Grover")
    assert len(del_results) == 0


@pytest.mark.asyncio
async def test_dag_branching_and_thread_reconstruction(test_db: Database) -> None:
    """Verifies DAG tree message branching, sibling metrics, and linear thread reconstruction."""
    repo = ConversationRepository(test_db)

    conv = await repo.create_conversation(title="Branching DAG Test", conversation_id="c_branch_01")

    # Root user message U1
    u1 = await repo.add_message(conversation_id=conv.id, role="user", content="Question 1")
    # Assistant response A1
    a1 = await repo.add_message(conversation_id=conv.id, role="assistant", content="Answer 1", parent_id=u1.id)

    # Branch 1: User message U2
    u2 = await repo.add_message(conversation_id=conv.id, role="user", content="Question 2 (Branch 1)", parent_id=a1.id)
    # Assistant response A2
    a2 = await repo.add_message(conversation_id=conv.id, role="assistant", content="Answer 2 (Branch 1)", parent_id=u2.id)

    # Branch 2: Fork edit from U2 -> creates sibling U2_alt sharing parent A1
    u2_alt, _ = await repo.create_branch_edit(
        conversation_id=conv.id,
        target_message_id=u2.id,
        new_content="Question 2 (Branch 2 alternate)",
    )
    assert u2_alt.parent_id == a1.id

    # Assistant response A2_alt
    a2_alt = await repo.add_message(
        conversation_id=conv.id,
        role="assistant",
        content="Answer 2 (Branch 2 alternate)",
        parent_id=u2_alt.id,
    )

    # Check sibling indices
    idx1, total1 = await repo.get_message_siblings(u2.id)
    idx2, total2 = await repo.get_message_siblings(u2_alt.id)
    assert total1 == 2
    assert total2 == 2
    assert idx1 == 1
    assert idx2 == 2

    # Active thread currently points to A2_alt (most recent leaf)
    thread = await repo.get_active_thread(conv.id)
    thread_ids = [m.id for m in thread]
    assert thread_ids == [u1.id, a1.id, u2_alt.id, a2_alt.id]

    # Switch active branch back to U2
    resolved_leaf, switched_thread = await repo.switch_active_branch(conv.id, u2.id)
    assert resolved_leaf == a2.id
    switched_ids = [m.id for m in switched_thread]
    assert switched_ids == [u1.id, a1.id, u2.id, a2.id]


@pytest.mark.asyncio
async def test_thread_exporters_markdown_and_json(test_db: Database) -> None:
    """Verifies clean Markdown and structured JSON export outputs."""
    repo = ConversationRepository(test_db)
    conv = await repo.create_conversation(
        title="Export Test Conversation",
        model="gemini-2.5-pro",
        system_prompt="You are a helpful assistant.",
        conversation_id="c_export_01",
    )

    u1 = await repo.add_message(
        conversation_id=conv.id,
        role="user",
        content="Write code with unclosed backticks\n```python\nprint('hello')",
    )
    a1 = await repo.add_message(
        conversation_id=conv.id,
        role="assistant",
        content="Here is code:\n```python\nprint('done')\n```",
        thoughts="Let me think about how to formulate the response.",
        parent_id=u1.id,
    )

    thread = await repo.get_active_thread(conv.id)

    # Markdown export test
    md = ThreadExporter.export_to_markdown(conv, thread)
    assert md.startswith("---")
    assert 'title: "Export Test Conversation"' in md
    assert "### User" in md
    assert "### Gemini (gemini-2.5-pro)" in md
    assert "<details>" in md
    assert "<summary>Thought Process</summary>" in md
    assert "Let me think about how to formulate" in md
    # Assert code block fence balancing closed the unclosed fence
    assert md.count("```") % 2 == 0

    # JSON export test
    all_msgs = await repo.get_all_messages(conv.id)
    json_str = ThreadExporter.export_to_json(conv, thread, all_messages=all_msgs)
    data = json.loads(json_str)
    assert data["$schema"] == "https://gemini-local.internal/schemas/v1/conversation-export.json"
    assert data["conversation"]["id"] == conv.id
    assert len(data["active_branch"]) == 2
    assert str(u1.id) in data["branch_tree"]
    assert str(a1.id) in data["branch_tree"]


@pytest.mark.asyncio
async def test_remote_conversation_reconciliation_and_hydration(test_db: Database) -> None:
    """Verifies reconciliation with Gemini remote conversations and turn hydration."""
    repo = ConversationRepository(test_db)

    # Mock remote conversations
    remote_data = [
        {"cid": "c_rem_1", "title": "Remote Chat 1", "updated_usec": 1700000000000000, "is_pinned": True},
        {"cid": "c_rem_2", "title": "Remote Chat 2", "updated_usec": 1700000005000000, "is_pinned": False},
    ]

    res = await repo.reconcile_remote_conversations(remote_data)
    assert res["synced"] == 2
    assert res["new"] == 2
    assert res["updated"] == 0

    # Check local conversations created
    conv1 = await repo.get_conversation_by_remote_id("c_rem_1")
    assert conv1 is not None
    assert conv1.title == "Remote Chat 1"
    assert conv1.is_pinned is True

    # Re-sync with title update
    remote_data_updated = [
        {"cid": "c_rem_1", "title": "Remote Chat 1 (Renamed)", "updated_usec": 1700000010000000, "is_pinned": True},
        {"cid": "c_rem_2", "title": "Remote Chat 2", "updated_usec": 1700000005000000, "is_pinned": False},
    ]
    res2 = await repo.reconcile_remote_conversations(remote_data_updated)
    assert res2["updated"] == 1
    assert res2["unchanged"] == 1

    # Turn hydration
    turns_payload = [
        "c_rem_1",
        [
            ["turn_001", "user", [["text", "Hello remote Gemini"]], 1700000000000, "rid_1", "rcid_1"],
            ["turn_002", "model", [["text", "Hello from Gemini Web!"]], 1700000001000, "rid_2", "rcid_2"],
        ],
    ]
    inserted = await repo.hydrate_remote_turns(conv1.id, turns_payload)
    assert inserted == 2

    # Verify messages inserted and linked
    thread = await repo.get_active_thread(conv1.id)
    assert len(thread) == 2
    assert thread[0].content == "Hello remote Gemini"
    assert thread[1].content == "Hello from Gemini Web!"
    assert thread[1].parent_id == thread[0].id


@pytest.mark.asyncio
async def test_storage_scale_and_search_benchmark(test_db: Database) -> None:
    """
    Performance Benchmark:
    - Seeds 1,000 conversations with 11,000 messages.
    - Asserts bulk insert completes in < 5.0 seconds.
    - Asserts search query latency < 50ms across diverse queries.
    """
    search_engine = SearchEngine(test_db)
    now_ms = int(time.time() * 1000)

    topics = [
        ("Quantum", "Explain Shor quantum factoring", "def qft(circuit): pass"),
        ("Machine Learning", "Backpropagation through attention layer", "class Attention(nn.Module): pass"),
        ("Databases", "SQLite WAL mode concurrent reader performance", "PRAGMA journal_mode = WAL;"),
        ("Systems", "Tokio epoll runtime event loop on Linux", "async fn handle() -> io::Result<()>"),
        ("Web", "NullPointerException during React hydration", "throw new NullPointerException();"),
        ("Security", "AES-256-GCM authenticated encryption", "from cryptography.hazmat.primitives.ciphers.aead import AESGCM"),
        ("International", "Optimization of full-text search FTS5", "SELECT * FROM messages_fts;"),
    ]

    conv_rows = []
    msg_rows = []
    msg_id = 1

    for c in range(1, 1001):
        c_id = f"conv_{c:04d}"
        topic, prompt, code = topics[c % len(topics)]
        conv_rows.append((c_id, f"{topic} #{c}", "gemini-2.5-pro", None, None, None, None, 0, 0, now_ms - c * 1000, now_ms, None))

        # 11 messages per conversation = 11,000 messages
        msg_rows.append((c_id, None, "system", f"System prompt for {topic}", None, "gemini-2.5-pro", None, 0, now_ms, now_ms, None))
        parent = msg_id
        msg_id += 1

        for turn in range(5):
            msg_rows.append((c_id, parent, "user", f"{prompt} turn {turn+1}", None, "gemini-2.5-pro", None, 0, now_ms + turn * 100, now_ms + turn * 100, None))
            parent = msg_id
            msg_id += 1

            msg_rows.append((c_id, parent, "assistant", f"Response: {code}", "Deep reasoning", "gemini-2.5-pro", None, 0, now_ms + turn * 100 + 50, now_ms + turn * 100 + 50, None))
            parent = msg_id
            msg_id += 1

    t_seed_start = time.perf_counter()
    async with test_db.transaction() as conn:
        await conn.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            conv_rows,
        )
        await conn.executemany(
            "INSERT INTO messages (conversation_id, parent_id, role, content, thoughts, model, token_count, is_error, created_at, updated_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            msg_rows,
        )
    t_seed_dur = time.perf_counter() - t_seed_start
    assert t_seed_dur < 5.0, f"Seeding took {t_seed_dur:.2f}s, exceeding 5.0s limit"

    benchmark_queries = [
        "quantum",
        "factoring",
        "Attention",
        "Tokio epoll",
        "NullPointerException",
        "AESGCM",
        "FTS5",
        "Optimization",
        "C++20",
        "Error: NullPointerException",
        "nonexistent_token_benchmark_xyz",
    ]

    latencies: List[float] = []
    for q in benchmark_queries:
        t0 = time.perf_counter()
        results = await search_engine.search(q, limit=20)
        dur_ms = (time.perf_counter() - t0) * 1000
        latencies.append(dur_ms)
        assert isinstance(results, list)
        assert dur_ms < 50.0, f"Query '{q}' took {dur_ms:.2f}ms (> 50ms SLA)!"

    avg_lat = sum(latencies) / len(latencies)
    max_lat = max(latencies)
    assert avg_lat < 25.0, f"Average query latency {avg_lat:.2f}ms exceeded 25ms threshold"
    assert max_lat < 50.0, f"Max query latency {max_lat:.2f}ms exceeded 50ms SLA"


@pytest.mark.asyncio
async def test_database_teardown_zero_leaks_windows_unlink(tmp_path: Path) -> None:
    """Verifies that closing Database shuts down reader pool and frees all handles, allowing immediate file unlink."""
    db_file = tmp_path / "teardown_unlink_test.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)

    # Exercise reader pool across multiple queries
    res = await db.fetch_all("SELECT count(*) AS total FROM conversations;")
    assert len(res) == 1
    assert res[0]["total"] == 0

    # Close database and verify zero WinError 32 leaks
    await db.close()

    assert db_file.exists()
    db_file.unlink()  # Must not raise PermissionError: [WinError 32]
    assert not db_file.exists()


@pytest.mark.asyncio
async def test_database_modifying_sql_classification_and_mutation_notifications(tmp_path: Path) -> None:
    """Verifies that modifying SQL queries with leading comments or CTEs properly acquire lock and trigger mutation listeners."""
    db_file = tmp_path / "mod_classification_test.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)

    notified_count = 0

    def on_mutate():
        nonlocal notified_count
        notified_count += 1

    db.register_mutation_listener(on_mutate)
    now_ms = int(time.time() * 1000)

    # 1. Leading line comment before INSERT
    await db.execute(
        "-- leading line comment\nINSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?);",
        ("c_comm_1", "Comment Title 1", now_ms, now_ms),
    )
    assert notified_count == 1

    # 2. Leading block comment before UPDATE
    await db.execute(
        "/* multi-line\nblock comment */ UPDATE conversations SET title = ? WHERE id = ?;",
        ("Updated Title 1", "c_comm_1"),
    )
    assert notified_count == 2

    # 3. CTE modifying statement
    await db.execute(
        "WITH cte AS (SELECT 1) INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?);",
        ("c_cte_1", "CTE Title 2", now_ms, now_ms),
    )
    assert notified_count == 3

    # 4. Leading comment before SELECT must NOT trigger mutation listener
    res = await db.execute("-- read comment\nSELECT count(*) FROM conversations;")
    rows = await res.fetchall()
    assert len(rows) == 1
    assert notified_count == 3

    await db.close()
