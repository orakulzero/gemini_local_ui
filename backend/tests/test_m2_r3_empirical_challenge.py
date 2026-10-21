"""
backend/tests/test_m2_r3_empirical_challenge.py
Milestone M2 Round 3 Adversarial Empirical Challenge Test Suite.

Author: m2_r3_challenger_1 (Empirical Challenger: Critic / Specialist)
Target: Generation epoch cache invalidation in backend/app/storage/search.py
        Concurrent write bursts, race conditions, latency SLA (<50ms).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import threading
import time
from typing import AsyncIterator, List
import pytest
import pytest_asyncio

from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.search import SearchEngine


@pytest_asyncio.fixture
async def r3_db(tmp_path: Path) -> AsyncIterator[Database]:
    """Provides an isolated disk-backed SQLite database with full schema and WAL mode."""
    db_file = tmp_path / "m2_r3_challenge.db"
    db = Database(db_file)
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)
    yield db
    await db.close()


async def _seed_1000_convs(db: Database) -> None:
    """Seeds 1,000 conversations with 11 messages each (total 11,000 messages)."""
    now_ms = int(time.time() * 1000)
    topics = [
        ("Quantum", "Explain Shor quantum factoring algorithm", "def qft(circuit): pass"),
        ("Machine Learning", "Backpropagation through transformer attention layer", "class Attention(nn.Module): pass"),
        ("Databases", "SQLite WAL mode concurrent reader performance", "PRAGMA journal_mode = WAL;"),
        ("Systems", "Tokio epoll runtime event loop on Linux kernel", "async fn handle() -> io::Result<()>"),
        ("Web", "NullPointerException during React hydration phase", "throw new NullPointerException();"),
        ("Security", "AES-256-GCM authenticated encryption standard", "from cryptography.hazmat.primitives.ciphers.aead import AESGCM"),
        ("International", "Optimization of full-text search FTS5 indexes", "SELECT * FROM messages_fts;"),
        ("Compiler", "LLVM SSA intermediate representation optimization passes", "define i32 @main() { ret i32 0 }"),
        ("Network", "TCP congestion control BBR versus CUBIC bandwidth", "socket.setsockopt(IPPROTO_TCP, TCP_CONGESTION)"),
        ("Distributed", "Raft consensus log replication and leader election", "func (r *Raft) AppendEntries() bool"),
    ]

    conv_rows = []
    msg_rows = []
    msg_id = 1

    for c in range(1, 1001):
        c_id = f"c_r3_{c:04d}"
        topic, prompt, code = topics[c % len(topics)]
        conv_rows.append((c_id, f"{topic} #{c}", "gemini-2.5-pro", None, None, None, None, 0, 0, now_ms - c * 1000, now_ms, None))

        msg_rows.append((c_id, None, "system", f"System prompt for {topic} conversation {c}", None, "gemini-2.5-pro", None, 0, now_ms, now_ms, None))
        parent = msg_id
        msg_id += 1

        for turn in range(5):
            msg_rows.append((c_id, parent, "user", f"{prompt} turn {turn+1} sample query {c}", None, "gemini-2.5-pro", None, 0, now_ms + turn * 100, now_ms + turn * 100, None))
            parent = msg_id
            msg_id += 1

            msg_rows.append((c_id, parent, "assistant", f"Response for {topic}: {code} turn {turn+1}", f"Deep reasoning trace for {topic} {c}", "gemini-2.5-pro", None, 0, now_ms + turn * 100 + 50, now_ms + turn * 100 + 50, None))
            parent = msg_id
            msg_id += 1

    async with db.transaction() as conn:
        await conn.executemany(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            conv_rows,
        )
        await conn.executemany(
            "INSERT INTO messages (conversation_id, parent_id, role, content, thoughts, model, token_count, is_error, created_at, updated_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            msg_rows,
        )


@pytest.mark.asyncio
async def test_r3_concurrent_write_burst_latency_sla(r3_db: Database) -> None:
    """
    Adversarial Stress:
    Fires 100 concurrent searches while simultaneously sustaining a burst of
    30 continuous write transactions against an 11,000 message database.
    Verifies:
    1. Zero SQLite busy or lock errors.
    2. P99 latency SLA strictly < 50ms across the search queries under write burst.
    """
    await _seed_1000_convs(r3_db)
    repo = ConversationRepository(r3_db)
    search_engine = SearchEngine(r3_db)

    # Warmup
    await search_engine.search("quantum")

    search_queries = [
        "quantum", "factoring", "Attention", "Tokio epoll",
        "NullPointerException", "AESGCM", "FTS5", "Optimization",
        "Raft consensus", "congestion control",
    ]

    latencies: List[float] = []
    search_errors: List[Exception] = []
    write_errors: List[Exception] = []

    stop_writes = False

    async def write_burst_worker():
        step = 0
        while not stop_writes and step < 30:
            step += 1
            cid = f"c_burst_{step:04d}"
            try:
                conv = await repo.create_conversation(f"Burst Conv {step}", cid)
                await repo.add_message(
                    conversation_id=conv.id,
                    role="user",
                    content=f"Burst payload token_{step:04d} content",
                )
            except Exception as e:
                write_errors.append(e)
            await asyncio.sleep(0.002)

    async def search_worker(query: str):
        t0 = time.perf_counter()
        try:
            res = await search_engine.search(query, limit=20)
            dt_ms = (time.perf_counter() - t0) * 1000
            latencies.append(dt_ms)
            assert isinstance(res, list)
        except Exception as e:
            search_errors.append(e)

    # Start write burst in background
    write_task = asyncio.create_task(write_burst_worker())

    # Launch 100 concurrent searches
    search_tasks = [
        asyncio.create_task(search_worker(search_queries[i % len(search_queries)]))
        for i in range(100)
    ]

    await asyncio.gather(*search_tasks)
    stop_writes = True
    await write_task

    assert len(search_errors) == 0, f"Search errors under write burst: {search_errors}"
    assert len(write_errors) == 0, f"Write errors during search burst: {write_errors}"

    latencies.sort()
    min_lat = latencies[0]
    p50_lat = latencies[int(len(latencies) * 0.50)]
    p90_lat = latencies[int(len(latencies) * 0.90)]
    p95_lat = latencies[int(len(latencies) * 0.95)]
    p99_lat = latencies[int(len(latencies) * 0.99)]
    max_lat = latencies[-1]

    print("\n[R3 BENCHMARK] 100 Concurrent Searches during 30 Write Bursts:")
    print(f"  Min Latency: {min_lat:.2f} ms")
    print(f"  P50 Latency: {p50_lat:.2f} ms")
    print(f"  P90 Latency: {p90_lat:.2f} ms")
    print(f"  P95 Latency: {p95_lat:.2f} ms")
    print(f"  P99 Latency: {p99_lat:.2f} ms")
    print(f"  Max Latency: {max_lat:.2f} ms")

    assert p99_lat < 50.0, f"P99 latency {p99_lat:.2f}ms violated 50ms SLA under write bursts!"


@pytest.mark.asyncio
async def test_r3_coalesced_in_flight_queries_under_burst_mutations(r3_db: Database) -> None:
    """
    Stress-test single-flight coalescing and generation epoch invalidation:
    Ensures that when 20 coroutines coalesce on the exact same search key
    while mutations occur, no corrupt or stale state enters the cache.
    """
    repo = ConversationRepository(r3_db)
    search_engine = SearchEngine(r3_db)

    conv = await repo.create_conversation("Coalesce Race Conv", "c_coalesce_01")
    await repo.add_message(
        conversation_id=conv.id,
        role="user",
        content="Initial message with CoalesceCanaryAlpha",
    )

    original_fetch = r3_db._sync_fetch_all
    in_flight_count = 0
    in_flight_lock = threading.Lock()
    fetch_paused = threading.Event()
    allow_complete = threading.Event()

    def hooked_fetch(query: str, params: any):
        nonlocal in_flight_count
        with in_flight_lock:
            in_flight_count += 1
        fetch_paused.set()
        allow_complete.wait(timeout=5.0)
        return original_fetch(query, params)

    r3_db._sync_fetch_all = hooked_fetch

    try:
        # Launch 20 concurrent identical searches
        tasks = [
            asyncio.create_task(search_engine.search("CoalesceCanaryAlpha"))
            for _ in range(20)
        ]

        # Wait until the leader query reaches hooked_fetch
        fetch_paused.wait(timeout=5.0)

        # Mutate the database while queries are in-flight!
        await repo.add_message(
            conversation_id=conv.id,
            role="assistant",
            content="Second response with CoalesceCanaryAlpha",
        )

        # Allow leader query to complete
        allow_complete.set()

        results = await asyncio.gather(*tasks)

        # All 20 coalesced queries should return the exact same result list
        first_len = len(results[0])
        for r in results:
            assert len(r) == first_len

        # Since generation advanced during in-flight execution, cache MUST be empty
        assert len(search_engine._cache) == 0, "Cache populated with stale in-flight results!"

        # Subsequent search MUST return 2 results from fresh DB query
        fresh_res = await search_engine.search("CoalesceCanaryAlpha")
        assert len(fresh_res) == 2, f"Expected 2 fresh results, got {len(fresh_res)}"
        # And now fresh_res SHOULD be cached
        assert len(search_engine._cache) == 1

    finally:
        r3_db._sync_fetch_all = original_fetch
        allow_complete.set()


@pytest.mark.asyncio
async def test_r3_rapid_epoch_churn_and_monotonicity(r3_db: Database) -> None:
    """
    Verifies that 100 rapid sequential mutations correctly advance _generation monotonically
    and that intermediate searches consistently observe up-to-date counts without stale cache pollution.
    """
    repo = ConversationRepository(r3_db)
    search_engine = SearchEngine(r3_db)

    conv = await repo.create_conversation("Epoch Monotonicity", "c_mono_01")
    expected_count = 0

    initial_gen = search_engine._generation

    for i in range(1, 51):
        await repo.add_message(
            conversation_id=conv.id,
            role="user" if i % 2 == 1 else "assistant",
            content=f"Message {i} mentioning MonotonicCanaryToken",
        )
        expected_count += 1

        # Check generation advanced strictly monotonically (INSERT messages + UPDATE conversations = 2 increments)
        assert search_engine._generation == initial_gen + 2 * i

        # Perform search with limit=100
        res = await search_engine.search("MonotonicCanaryToken", limit=100)
        assert len(res) == expected_count, (
            f"Step {i}: expected {expected_count} results, got {len(res)}"
        )
        # Search is cached
        assert len(search_engine._cache) >= 1
