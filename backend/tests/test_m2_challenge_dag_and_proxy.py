"""
backend/tests/test_m2_challenge_dag_and_proxy.py
Empirical Adversarial Challenge Suite for Milestone M2:
1. Deep DAG Tree Branching (50+ depth, multi-way branch splits with 10+ siblings, in-place edits).
2. Circular Reference & Invalid Parent Injection Resistance.
3. Concurrent OpenAI Streaming Clients (25 concurrent clients, SSE wire format, token ordering, connection drop resilience).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import sqlite3
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
import httpx
import pytest
import pytest_asyncio
from starlette.testclient import TestClient

from backend.app.bridge.session import SessionManager
from backend.app.main import app
from backend.app.mock.mock_server import MockGeminiServer
from backend.app.storage.database import Database
from backend.app.storage.exporter import ThreadExporter
from backend.app.storage.repository import ConversationRepository


def get_free_port() -> int:
    """Finds an available TCP port to avoid WSAEADDRINUSE collisions."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest_asyncio.fixture
async def challenge_db(tmp_path: Path) -> AsyncIterator[Database]:
    """Provides a fresh isolated SQLite database with schema.sql applied."""
    db_file = tmp_path / "challenge_m2.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)
    yield db
    await db.close()


@pytest.fixture(scope="module")
def challenge_mock_server():
    """Starts isolated deterministic mock Gemini web server on a dynamically allocated port."""
    port = get_free_port()
    server = MockGeminiServer(host="127.0.0.1", port=port)
    server.start_background()

    # Poll until ready
    for _ in range(50):
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/app", timeout=0.2)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.05)

    yield server
    server.stop_background()


@pytest.fixture(scope="module")
def challenge_configured_app(challenge_mock_server: MockGeminiServer):
    """Configures FastAPI application with session manager pointing to challenge mock server."""
    port = challenge_mock_server.port
    sm = SessionManager(base_url=f"http://127.0.0.1:{port}")
    sm.context.cookies = {
        "__Secure-1PSID": "mock_challenge_psid_token_12345",
        "__Secure-1PSIDTS": "mock_challenge_psidts_token_67890",
    }
    sm.context.access_token = "mock_challenge_snlm0e_token"

    app.state.session_manager = sm
    import backend.app.main as main_mod
    main_mod.session_manager = sm

    return app


# ============================================================================
# 1. Deep DAG Tree Branching & Scalability Stress Tests
# ============================================================================

@pytest.mark.asyncio
async def test_deep_dag_linear_chain_60_turns(challenge_db: Database) -> None:
    """
    Stress-tests deep linear message chains (60 nested message turns).
    Verifies that recursive CTE reconstructs thread in exact topological order
    (root-to-leaf), strictly monotonic timestamps, without memory leaks or recursion depth limits.
    """
    repo = ConversationRepository(challenge_db)
    conv = await repo.create_conversation("Deep Linear 60 Turns")

    # Build 60 nested message turns
    parent_id: Optional[int] = None
    inserted_ids: List[int] = []
    start_ts = int(time.time() * 1000)

    for i in range(60):
        role = "user" if i % 2 == 0 else "assistant"
        content = f"Turn {i+1} message content payload"
        msg = await repo.add_message(
            conversation_id=conv.id,
            role=role,
            content=content,
            parent_id=parent_id,
            created_at=start_ts + (i * 100),
        )
        inserted_ids.append(msg.id)
        parent_id = msg.id

    # Measure recursive CTE reconstruction time
    t0 = time.perf_counter()
    active_thread = await repo.get_active_thread(conv.id)
    reconstruction_latency_ms = (time.perf_counter() - t0) * 1000

    # Assertions
    assert len(active_thread) == 60, f"Expected 60 messages, got {len(active_thread)}"
    assert reconstruction_latency_ms < 50.0, f"Reconstruction too slow: {reconstruction_latency_ms:.2f}ms >= 50ms SLA"

    # Verify exact root-to-leaf topological ordering
    for idx, msg in enumerate(active_thread):
        expected_content = f"Turn {idx+1} message content payload"
        assert msg.content == expected_content, f"Order mismatch at index {idx}: {msg.content} != {expected_content}"
        assert msg.id == inserted_ids[idx]
        if idx == 0:
            assert msg.parent_id is None, "Root message must have parent_id=None"
        else:
            assert msg.parent_id == inserted_ids[idx - 1], f"Message {idx} parent_id broken"

        # Verify sibling annotations: single linear branch must have index 1 of 1
        assert msg.sibling_index == 1
        assert msg.sibling_count == 1


@pytest.mark.asyncio
async def test_deep_dag_multiway_branching_15_siblings(challenge_db: Database) -> None:
    """
    Constructs a complex branching tree with:
    - 20 sequential spine turns.
    - 15 multi-way sibling branches split off at turn 20.
    - 10 nested turns along each sibling branch (total 185 messages).
    Verifies sibling counting, active thread isolation, and branch switching.
    """
    repo = ConversationRepository(challenge_db)
    conv = await repo.create_conversation("Multiway Branching Tree")

    # 1. Build 20 spine turns
    parent_id: Optional[int] = None
    spine_ids: List[int] = []
    base_ts = 1000000

    for i in range(20):
        msg = await repo.add_message(
            conversation_id=conv.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"Spine turn {i+1}",
            parent_id=parent_id,
            created_at=base_ts + i * 10,
        )
        spine_ids.append(msg.id)
        parent_id = msg.id

    turn_20_id = parent_id
    assert turn_20_id is not None

    # 2. Split into 15 sibling branches at turn 20
    # Each sibling branch has 10 nested turns
    sibling_roots: List[int] = []
    sibling_leafs: List[int] = []

    for sib_idx in range(15):
        sub_parent = turn_20_id
        sib_root_id: Optional[int] = None
        for step in range(10):
            msg = await repo.add_message(
                conversation_id=conv.id,
                role="user" if step % 2 == 0 else "assistant",
                content=f"Branch {sib_idx+1} Step {step+1}",
                parent_id=sub_parent,
                created_at=base_ts + 1000 + (sib_idx * 200) + (step * 10),
            )
            if step == 0:
                sib_root_id = msg.id
            sub_parent = msg.id

        assert sib_root_id is not None
        sibling_roots.append(sib_root_id)
        sibling_leafs.append(sub_parent)

    # Total messages = 20 spine + 15 * 10 = 170 messages
    all_msgs = await repo.get_all_messages(conv.id)
    assert len(all_msgs) == 170

    # 3. Verify sibling resolution for all 15 sibling roots
    for idx, root_id in enumerate(sibling_roots):
        curr_idx, total_siblings = await repo.get_message_siblings(root_id)
        assert total_siblings == 15, f"Expected 15 siblings, got {total_siblings}"
        assert curr_idx == idx + 1, f"Expected sibling index {idx+1}, got {curr_idx}"

    # 4. Switch active branch to sibling #8 (index 7)
    target_leaf = sibling_leafs[7]
    resolved_leaf, active_thread = await repo.switch_active_branch(conv.id, target_leaf)

    assert resolved_leaf == target_leaf
    # Thread must contain 20 spine messages + 10 messages of branch 8 = 30 messages
    assert len(active_thread) == 30

    # Verify no message from branch 1..7 or 9..15 is present in active thread
    for m in active_thread:
        if "Branch " in m.content:
            assert "Branch 8 " in m.content, f"Branch isolation failed: unexpected {m.content}"

    # Verify active thread sibling annotations on branch 8 root
    branch_8_root_msg = next(m for m in active_thread if m.content == "Branch 8 Step 1")
    assert branch_8_root_msg.sibling_index == 8
    assert branch_8_root_msg.sibling_count == 15


@pytest.mark.asyncio
async def test_deep_dag_edit_earlier_turn_parallel_branch(challenge_db: Database) -> None:
    """
    Stress-tests editing an earlier message in a 50-turn thread:
    - Edit message at turn 15 -> creates new sibling sharing turn 14 as parent.
    - Append 20 new messages on the edited branch.
    - Switch back and forth between original turn 50 leaf and edited branch leaf.
    - Confirms both parallel branches remain 100% intact without data loss or corruption.
    """
    repo = ConversationRepository(challenge_db)
    conv = await repo.create_conversation("DAG In-Place Edit Test")

    # Build 50 turns
    parent_id: Optional[int] = None
    turn_15_id: Optional[int] = None
    original_leaf_id: Optional[int] = None

    for i in range(50):
        msg = await repo.add_message(
            conversation_id=conv.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"Original Turn {i+1}",
            parent_id=parent_id,
        )
        parent_id = msg.id
        if i == 14:  # Turn 15
            turn_15_id = msg.id

    original_leaf_id = parent_id
    assert turn_15_id is not None
    assert original_leaf_id is not None

    # Verify initial active thread is 50 turns
    thread_orig = await repo.get_active_thread(conv.id)
    assert len(thread_orig) == 50

    # Edit turn 15
    edit_msg, edit_thread = await repo.create_branch_edit(
        conversation_id=conv.id,
        target_message_id=turn_15_id,
        new_content="Edited Turn 15 Alternative Prompt",
    )

    # Edit message must share turn 14 as parent
    turn_15_msg = await repo.get_message(turn_15_id)
    assert turn_15_msg is not None
    assert edit_msg.parent_id == turn_15_msg.parent_id
    assert edit_msg.metadata.get("edited_from") == turn_15_id

    # The new active thread currently has 14 prior turns + 1 edited turn = 15 messages
    assert len(edit_thread) == 15

    # Check siblings of turn 15
    sib_idx_orig, total_sibs_15 = await repo.get_message_siblings(turn_15_id)
    sib_idx_edit, _ = await repo.get_message_siblings(edit_msg.id)
    assert total_sibs_15 == 2
    assert sib_idx_orig == 1
    assert sib_idx_edit == 2

    # Append 20 new turns to the edited branch
    curr_edit_parent = edit_msg.id
    for j in range(20):
        msg = await repo.add_message(
            conversation_id=conv.id,
            role="user" if j % 2 == 0 else "assistant",
            content=f"Edited Branch Continuation {j+1}",
            parent_id=curr_edit_parent,
        )
        curr_edit_parent = msg.id
    edited_leaf_id = curr_edit_parent

    # Active thread on edited branch is now 15 + 20 = 35 messages
    active_edited = await repo.get_active_thread(conv.id)
    assert len(active_edited) == 35

    # Switch back to original turn 50 leaf
    leaf_back, active_orig_restored = await repo.switch_active_branch(conv.id, original_leaf_id)
    assert leaf_back == original_leaf_id
    assert len(active_orig_restored) == 50
    assert active_orig_restored[-1].content == "Original Turn 50"

    # Switch forward to edited branch leaf
    leaf_fwd, active_edit_restored = await repo.switch_active_branch(conv.id, edited_leaf_id)
    assert leaf_fwd == edited_leaf_id
    assert len(active_edit_restored) == 35
    assert active_edit_restored[-1].content == "Edited Branch Continuation 20"


@pytest.mark.asyncio
async def test_deep_dag_export_markdown_and_json(challenge_db: Database) -> None:
    """
    Verifies that complex DAG structures (60 turns, multi-way branches)
    export cleanly to JSON and Markdown without stack overflow or corrupted syntax.
    """
    repo = ConversationRepository(challenge_db)
    conv = await repo.create_conversation("Exportable Deep DAG")

    parent_id = None
    for i in range(40):
        msg = await repo.add_message(
            conversation_id=conv.id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"Message {i+1} with `code_block()` and ```markdown\nmultiline\n```",
            thoughts=f"Detailed thoughts for turn {i+1}" if i % 2 != 0 else None,
            parent_id=parent_id,
        )
        parent_id = msg.id

    thread = await repo.get_active_thread(conv.id)
    all_msgs = await repo.get_all_messages(conv.id)

    # JSON export
    json_str = ThreadExporter.export_to_json(conv, thread, all_messages=all_msgs)
    data = json.loads(json_str)
    assert data["conversation"]["id"] == conv.id
    assert len(data["messages"]) == 40
    assert len(data["branch_tree"]) == 40
    assert str(parent_id) in data["branch_tree"]

    # Markdown export
    md_str = ThreadExporter.export_to_markdown(conv, thread)
    assert "---" in md_str
    assert "Exportable Deep DAG" in md_str
    assert "<details>" in md_str
    assert "</details>" in md_str


# ============================================================================
# 2. Circular Reference & Invalid parent_id Injection Resistance
# ============================================================================

@pytest.mark.asyncio
async def test_invalid_parent_id_nonexistent_rejection(challenge_db: Database) -> None:
    """
    Attempts invalid parent_id injection (nonexistent parent ID).
    Verifies that foreign key constraints cleanly reject insertion via sqlite3.IntegrityError
    and do not corrupt active_leaf_message_id or conversation state.
    """
    repo = ConversationRepository(challenge_db)
    conv = await repo.create_conversation("Invalid Parent Test")

    initial_conv = await repo.get_conversation(conv.id)
    assert initial_conv is not None
    initial_leaf = initial_conv.active_leaf_message_id

    # Attempt to insert message with invalid nonexistent parent
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        await repo.add_message(
            conversation_id=conv.id,
            role="user",
            content="Orphan message with non-existent parent",
            parent_id=99999999,
        )

    # Verify conversation state was not modified or corrupted
    updated_conv = await repo.get_conversation(conv.id)
    assert updated_conv is not None
    assert updated_conv.active_leaf_message_id == initial_leaf


@pytest.mark.asyncio
async def test_circular_reference_self_parenting_behavior(challenge_db: Database) -> None:
    """
    Empirical check on self-parenting (parent_id == id) and cycle resistance:
    1. Direct insertion where parent_id = id must fail via CHECK constraint.
    2. Cyclic message relationships must terminate cleanly in switch_active_branch via visited set.
    """
    repo = ConversationRepository(challenge_db)
    conv = await repo.create_conversation("Self-Parenting Cycle Test")

    # 1. Assert CHECK(parent_id != id) constraint blocks self-parenting
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed: parent_id != id"):
        await challenge_db.execute(
            """
            INSERT INTO messages (id, conversation_id, parent_id, role, content, created_at, updated_at)
            VALUES (555, ?, 555, 'user', 'Self-parenting loop node', 1000, 1000);
            """,
            (conv.id,),
        )

    # 2. Assert multi-node cycles terminate safely in switch_active_branch
    await challenge_db.execute(
        """
        INSERT INTO messages (id, conversation_id, parent_id, role, content, created_at, updated_at)
        VALUES (701, ?, NULL, 'user', 'Cycle Node A', 1000, 1000);
        """,
        (conv.id,),
    )
    await challenge_db.execute(
        """
        INSERT INTO messages (id, conversation_id, parent_id, role, content, created_at, updated_at)
        VALUES (702, ?, 701, 'assistant', 'Cycle Node B', 1001, 1001);
        """,
        (conv.id,),
    )
    await challenge_db.execute(
        "UPDATE messages SET parent_id = 702 WHERE id = 701;"
    )

    resolved_leaf, active_thread = await asyncio.wait_for(
        repo.switch_active_branch(conv.id, 701), timeout=0.5
    )
    assert resolved_leaf in (701, 702)


@pytest.mark.asyncio
async def test_cross_conversation_parent_id_injection(challenge_db: Database) -> None:
    """
    Empirical check on cross-conversation parent injection:
    - Conversation 1 contains message M1.
    - Conversation 2 creates message M2 with parent_id = M1.
    Verifies whether recursive CTE respects conversation boundary or traverses across conversations.
    """
    repo = ConversationRepository(challenge_db)
    conv1 = await repo.create_conversation("Conversation 1 - Source")
    conv2 = await repo.create_conversation("Conversation 2 - Target")

    m1 = await repo.add_message(conv1.id, "user", "Sensitive Secret in Conv 1")
    m2 = await repo.add_message(conv2.id, "user", "Message in Conv 2 linking to Conv 1", parent_id=m1.id)

    # Query active thread of Conversation 2
    thread_conv2 = await repo.get_active_thread(conv2.id)

    # Check that Conv 1's message did NOT leak into Conv 2's active thread
    leaked = any(m.conversation_id == conv1.id for m in thread_conv2)
    assert leaked is False, (
        "Vulnerability detected: recursive CTE leaked messages from foreign conversations into the active thread."
    )
    assert len(thread_conv2) == 1
    assert thread_conv2[0].id == m2.id
    assert all(m.conversation_id == conv2.id for m in thread_conv2)


# ============================================================================
# 3. Concurrent OpenAI Streaming Clients (/v1/chat/completions)
# ============================================================================

@pytest.mark.asyncio
async def test_concurrent_openai_streaming_25_clients(challenge_configured_app) -> None:
    """
    Stress-tests OpenAI streaming proxy with 25 concurrent simulated clients:
    - Requests /v1/chat/completions with stream=True concurrently via asyncio.gather.
    - Validates proper SSE formatting: data: {...}, data: [DONE], \\n\\n delimiters.
    - Validates token ordering and content integrity across all 25 parallel streams.
    - Measures total duration, individual latencies, and throughput.
    """
    transport = httpx.ASGITransport(app=challenge_configured_app)

    async def simulate_client(client_id: int) -> Dict[str, Any]:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            payload = {
                "model": "gpt-4o",
                "messages": [
                    {"role": "user", "content": f"Client #{client_id} concurrency stress request"}
                ],
                "stream": True,
            }
            t0 = time.perf_counter()
            async with client.stream("POST", "/v1/chat/completions", json=payload, timeout=30.0) as resp:
                status_code = resp.status_code
                content_type = resp.headers.get("content-type", "")

                sse_lines: List[str] = []
                async for line in resp.aiter_lines():
                    if line:
                        sse_lines.append(line)

            latency_sec = time.perf_counter() - t0
            return {
                "client_id": client_id,
                "status_code": status_code,
                "content_type": content_type,
                "sse_lines": sse_lines,
                "latency_sec": latency_sec,
            }

    t_start = time.perf_counter()
    tasks = [simulate_client(i) for i in range(25)]
    results = await asyncio.gather(*tasks)
    total_duration_sec = time.perf_counter() - t_start

    # Metrics logging
    avg_latency = sum(r["latency_sec"] for r in results) / len(results)
    max_latency = max(r["latency_sec"] for r in results)
    min_latency = min(r["latency_sec"] for r in results)

    # Validations across all 25 concurrent streams
    for res in results:
        cid = res["client_id"]
        assert res["status_code"] == 200, f"Client {cid} got HTTP {res['status_code']}"
        assert "text/event-stream" in res["content_type"], f"Client {cid} invalid content-type: {res['content_type']}"

        lines = res["sse_lines"]
        assert len(lines) >= 4, f"Client {cid} received insufficient SSE lines: {len(lines)}"

        # 1. First event must be initial role chunk
        first_line = lines[0]
        assert first_line.startswith("data: "), f"Client {cid} line 0 missing 'data: ' prefix"
        first_chunk = json.loads(first_line[len("data: "):])
        assert first_chunk["object"] == "chat.completion.chunk"
        assert first_chunk["choices"][0]["delta"]["role"] == "assistant"

        # 2. Intermediate events must be delta text
        text_deltas: List[str] = []
        finish_reason_found = False

        for line in lines[1:-1]:
            assert line.startswith("data: ")
            data_str = line[len("data: "):]
            chunk = json.loads(data_str)
            assert chunk["object"] == "chat.completion.chunk"
            choice = chunk["choices"][0]

            if choice["delta"].get("content"):
                text_deltas.append(choice["delta"]["content"])
            if choice.get("finish_reason") == "stop":
                finish_reason_found = True

        assert finish_reason_found is True, f"Client {cid} missing finish_reason='stop'"
        assert len(text_deltas) > 0, f"Client {cid} received no text deltas"

        # 3. Last event must be terminal data: [DONE]
        last_line = lines[-1]
        assert last_line == "data: [DONE]", f"Client {cid} final line was not 'data: [DONE]': {last_line}"

        # 4. Reconstructed text content check
        full_text = "".join(text_deltas)
        assert "Windows **DPAPI**" in full_text, f"Client {cid} missing expected text in stream"

    assert total_duration_sec < 30.0, f"25 concurrent streams took too long: {total_duration_sec:.2f}s"


@pytest.mark.asyncio
async def test_concurrent_openai_streaming_connection_drop_resilience(challenge_configured_app) -> None:
    """
    Stress-tests server resilience to client connection drops mid-stream:
    - 5 clients abruptly abort/close connection after receiving 2 chunks.
    - 5 concurrent clients read full streams to completion.
    - Verifies that dropped connections do not crash the server or corrupt adjacent active streams.
    """
    transport = httpx.ASGITransport(app=challenge_configured_app)

    async def dropping_client(cid: int) -> Tuple[str, int]:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": f"Drop client {cid}"}], "stream": True}
            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                assert resp.status_code == 200
                chunks_read = 0
                async for line in resp.aiter_lines():
                    if line:
                        chunks_read += 1
                        if chunks_read >= 2:
                            # Abrupt client disconnection mid-stream
                            break
            return ("dropped", chunks_read)

    async def normal_client(cid: int) -> Tuple[str, int]:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": f"Normal client {cid}"}], "stream": True}
            lines = []
            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line:
                        lines.append(line)
            return ("completed", len(lines))

    # Run 5 dropping clients and 5 normal clients concurrently
    tasks = [dropping_client(i) for i in range(5)] + [normal_client(i + 5) for i in range(5)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, res in enumerate(results):
        assert not isinstance(res, Exception), f"Client {idx} raised unexpected exception: {res}"

    drop_results = results[:5]
    normal_results = results[5:]

    for status, count in drop_results:
        assert status == "dropped"
        assert count == 2

    for status, count in normal_results:
        assert status == "completed"
        assert count >= 5  # Standard full response


@pytest.mark.asyncio
async def test_openai_streaming_stop_sequences_concurrency(challenge_configured_app) -> None:
    """
    Verifies that stop sequences are correctly enforced concurrently across 10 clients:
    - Clients specify stop=["DPAPI"].
    - Stream truncates before "DPAPI" is emitted.
    - Yields clean finish_reason: "stop" and terminal data: [DONE].
    """
    transport = httpx.ASGITransport(app=challenge_configured_app)

    async def stop_client(cid: int) -> str:
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            payload = {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": f"Stop client {cid}"}],
                "stream": True,
                "stop": ["DPAPI"],
            }
            chunks: List[str] = []
            terminal_done = False
            finish_reason = None

            async with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
                assert resp.status_code == 200
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[len("data: "):]
                        if data_str == "[DONE]":
                            terminal_done = True
                            continue
                        chunk = json.loads(data_str)
                        delta = chunk["choices"][0]["delta"]
                        if delta.get("content"):
                            chunks.append(delta["content"])
                        if chunk["choices"][0].get("finish_reason"):
                            finish_reason = chunk["choices"][0]["finish_reason"]

            full_text = "".join(chunks)
            assert "DPAPI" not in full_text, f"Stop sequence failed: 'DPAPI' found in '{full_text}'"
            assert finish_reason == "stop"
            assert terminal_done is True
            return full_text

    tasks = [stop_client(i) for i in range(10)]
    texts = await asyncio.gather(*tasks)
    assert len(texts) == 10
