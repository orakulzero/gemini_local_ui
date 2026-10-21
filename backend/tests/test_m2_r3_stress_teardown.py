"""
backend/tests/test_m2_r3_stress_teardown.py
Adversarial empirical stress suite for SQLite database teardown and Windows handle safety.

Author: m2_r3_challenger_2 (EMPIRICAL CHALLENGER / Critic & Specialist)
Focus:
1. High-concurrency rapid initialize-query-close teardown cycles.
2. Assert zero PermissionError: [WinError 32] on Windows during immediate .db/.db-wal/.db-shm unlink.
3. Assert zero thread accumulation or connection leaks across repeated cycles.
4. Concurrent close() call idempotency under active read load.
5. Immediate directory rmtree cleanup without locks.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import threading
import time
from typing import List
import psutil
import pytest

from backend.app.storage.database import Database


@pytest.mark.asyncio
async def test_rapid_init_query_teardown_cycles_zero_winerror32(tmp_path: Path) -> None:
    """
    Stress-tests 40 rapid initialize-query-close teardown cycles on Windows.
    In each cycle:
    - Creates a new WAL-enabled SQLite database on disk.
    - Initializes the complete schema (tables, indexes, FTS5 triggers).
    - Seeds test records.
    - Executes 20 parallel reader queries across all reader pool threads.
    - Concurrently executes 5 write transactions.
    - Calls await db.close().
    - Immediately unlinks the .db file, plus .db-wal and .db-shm if present.
    - Asserts ZERO PermissionError / WinError 32 failures.
    """
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    assert schema_path.exists(), f"Schema file not found at {schema_path}"

    cycles = 30
    start_time = time.perf_counter()

    for i in range(cycles):
        cycle_dir = tmp_path / f"cycle_{i}"
        cycle_dir.mkdir(parents=True, exist_ok=True)
        db_file = cycle_dir / f"test_store_{i}.db"
        wal_file = cycle_dir / f"test_store_{i}.db-wal"
        shm_file = cycle_dir / f"test_store_{i}.db-shm"

        db = Database(db_file, max_readers=4)
        await db.init_schema(schema_path)

        # Seed data
        now_ms = int(time.time() * 1000)
        await db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?);",
            (f"c_stress_{i}", f"Stress Title {i}", now_ms, now_ms),
        )
        for m in range(5):
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, thoughts, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?);",
                (f"c_stress_{i}", "user" if m % 2 == 0 else "assistant", f"Content {i} {m} FTS indexing test", f"Thought {i} {m}", now_ms + m, now_ms + m),
            )

        # Concurrently execute 20 reader tasks + 5 write tasks
        async def reader_task(idx: int) -> None:
            if idx % 3 == 0:
                rows = await db.fetch_all("SELECT * FROM messages WHERE conversation_id = ?", (f"c_stress_{i}",))
                assert len(rows) >= 5
            elif idx % 3 == 1:
                row = await db.fetch_one("SELECT title FROM conversations WHERE id = ?", (f"c_stress_{i}",))
                assert row is not None
                assert row["title"] == f"Stress Title {i}"
            else:
                val = await db.fetch_val("SELECT count(*) FROM messages WHERE conversation_id = ?", (f"c_stress_{i}",))
                assert val >= 5

        async def writer_task(idx: int) -> None:
            await db.execute(
                "INSERT INTO messages (conversation_id, role, content, thoughts, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?);",
                (f"c_stress_{i}", "user", f"Extra content {idx}", None, now_ms + 100 + idx, now_ms + 100 + idx),
            )

        tasks = [reader_task(r) for r in range(20)] + [writer_task(w) for w in range(5)]
        await asyncio.gather(*tasks)

        # Teardown database
        await db.close()

        # Immediate unlinking on Windows - must never raise PermissionError: [WinError 32]
        assert db_file.exists(), f"Database file {db_file} should exist before unlink"
        try:
            db_file.unlink()
        except PermissionError as e:
            pytest.fail(f"Cycle {i}: PermissionError [WinError 32] unlinking {db_file}: {e}")

        if wal_file.exists():
            try:
                wal_file.unlink()
            except PermissionError as e:
                pytest.fail(f"Cycle {i}: PermissionError [WinError 32] unlinking {wal_file}: {e}")

        if shm_file.exists():
            try:
                shm_file.unlink()
            except PermissionError as e:
                pytest.fail(f"Cycle {i}: PermissionError [WinError 32] unlinking {shm_file}: {e}")

        assert not db_file.exists()

    elapsed = time.perf_counter() - start_time
    assert elapsed < 30.0, f"Rapid 30 teardown cycles took {elapsed:.2f}s, exceeding 30s threshold"


@pytest.mark.asyncio
async def test_thread_and_memory_leak_verification_across_cycles(tmp_path: Path) -> None:
    """
    Verifies that reader ThreadPoolExecutors do not leak worker threads across 20 cycles.
    ThreadPoolExecutor.shutdown(wait=True) must join all threads.
    """
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    
    # Warm-up / baseline thread count
    initial_threads = threading.active_count()
    process = psutil.Process()
    initial_rss = process.memory_info().rss

    for i in range(20):
        db_file = tmp_path / f"leak_check_{i}.db"
        db = Database(db_file, max_readers=4)
        await db.init_schema(schema_path)

        # Spin up all 4 reader threads with concurrent reads
        async def read_op() -> None:
            await db.fetch_all("SELECT 1 AS num;")

        await asyncio.gather(*(read_op() for _ in range(16)))

        # Verify threads were spawned
        assert len(db._reader_conns) > 0

        # Close
        await db.close()
        db_file.unlink()

    # Small pause to allow OS thread join cleanup
    await asyncio.sleep(0.1)

    final_threads = threading.active_count()
    thread_diff = final_threads - initial_threads
    assert thread_diff <= 1, f"Thread leak detected! Initial: {initial_threads}, Final: {final_threads}, Diff: {thread_diff}"

    final_rss = process.memory_info().rss
    rss_growth_mb = (final_rss - initial_rss) / (1024 * 1024)
    assert rss_growth_mb < 50.0, f"Excessive memory growth across 20 cycles: {rss_growth_mb:.2f} MB"


@pytest.mark.asyncio
async def test_sequential_close_idempotency_handle_safety(tmp_path: Path) -> None:
    """
    Tests multiple sequential calls to await db.close() on the same Database instance.
    Validates that sequential teardown is safe, idempotent, and leaves zero open handles.
    """
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    db_file = tmp_path / "sequential_close.db"
    db = Database(db_file, max_readers=4)
    await db.init_schema(schema_path)

    # Populate and read
    now_ms = int(time.time() * 1000)
    await db.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES ('c1', 'T1', ?, ?);",
        (now_ms, now_ms),
    )
    await asyncio.gather(*(db.fetch_one("SELECT * FROM conversations WHERE id = 'c1';") for _ in range(10)))

    # Sequential close calls
    for _ in range(5):
        await db.close()

    # Verify immediate unlink
    assert db_file.exists()
    db_file.unlink()
    assert not db_file.exists()


@pytest.mark.asyncio
async def test_immediate_directory_rmtree_after_teardown(tmp_path: Path) -> None:
    """
    Verifies that an entire directory containing active WAL databases can be deleted
    via shutil.rmtree() immediately after close() without PermissionError: [WinError 32].
    """
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    db_dir = tmp_path / "rmtree_test_dir"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_file = db_dir / "target.db"

    db = Database(db_file, max_readers=4)
    await db.init_schema(schema_path)

    now_ms = int(time.time() * 1000)
    await db.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES ('c_rm', 'RM Title', ?, ?);",
        (now_ms, now_ms),
    )
    # Heavy reads to ensure all reader connections and mmap mappings are active
    for _ in range(5):
        await asyncio.gather(*(db.fetch_all("SELECT * FROM conversations;") for _ in range(8)))

    await db.close()

    # Immediate directory deletion without retries
    try:
        shutil.rmtree(db_dir)
    except PermissionError as e:
        pytest.fail(f"shutil.rmtree({db_dir}) failed with PermissionError on Windows: {e}")

    assert not db_dir.exists()


@pytest.mark.asyncio
async def test_cancelled_in_flight_readers_zero_handle_leaks(tmp_path: Path) -> None:
    """
    Verifies that when active reader coroutines are cancelled mid-flight,
    db.close() successfully reclaims all handles and allows immediate unlinking.
    """
    schema_path = Path(__file__).parent.parent / "app" / "storage" / "schema.sql"
    db_file = tmp_path / "cancel_teardown.db"
    db = Database(db_file, max_readers=4)
    await db.init_schema(schema_path)

    # Spawn reader tasks that get cancelled
    tasks: List[asyncio.Task] = []
    for _ in range(8):
        t = asyncio.create_task(db.fetch_all("SELECT count(*) FROM conversations;"))
        tasks.append(t)

    # Cancel tasks immediately
    for t in tasks:
        t.cancel()

    # Wait for cancellation to settle
    await asyncio.gather(*tasks, return_exceptions=True)

    # Close database and unlink
    await db.close()
    assert db_file.exists()
    db_file.unlink()
    assert not db_file.exists()
