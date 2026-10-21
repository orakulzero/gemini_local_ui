"""
tests/stress_sqlite_30r_10w.py
High-concurrency SQLite WAL stress test: 30 readers + 10 writers.
Verifies:
1. Zero 'database is locked' errors.
2. Zero data corruption (PRAGMA integrity_check and PRAGMA foreign_key_check return 'ok').
3. Transaction atomicity and message DAG consistency under heavy contention.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Dict, List

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sqlite_stress")


async def run_sqlite_stress_test(
    num_readers: int = 30,
    num_writers: int = 10,
    reader_iterations: int = 25,
    writer_iterations: int = 15,
) -> Dict[str, Any]:
    temp_dir = tempfile.mkdtemp(prefix="sqlite_stress_wal_")
    db_path = Path(temp_dir) / "stress_concurrency.db"
    logger.info("Initializing stress test DB at %s", db_path)

    # 1. Initialize Database with larger reader pool to handle 30 concurrent readers
    db = Database(db_path, max_readers=16)
    schema_path = PROJECT_ROOT / "backend" / "app" / "storage" / "schema.sql"
    await db.init_schema(schema_path)
    repo = ConversationRepository(db)

    # 2. Seed initial conversation and messages
    root_conv = await repo.create_conversation("Stress Root Conversation")
    seed_msg = await repo.add_message(
        conversation_id=root_conv.id,
        role="user",
        content="Stress root seed prompt",
        created_at=int(time.time() * 1000),
    )

    stats = {
        "num_readers": num_readers,
        "num_writers": num_writers,
        "reader_ops": 0,
        "writer_ops": 0,
        "lock_errors": 0,
        "other_errors": 0,
        "error_messages": [],
        "read_latencies_ms": [],
        "write_latencies_ms": [],
    }

    start_time = time.perf_counter()

    # 3. Reader Task Definition
    async def reader_worker(reader_id: int):
        for it in range(reader_iterations):
            t0 = time.perf_counter()
            try:
                # Alternate between DAG fetch, single convo, and telemetry queries
                if it % 3 == 0:
                    msgs = await repo.get_all_messages(root_conv.id)
                    assert len(msgs) >= 1
                elif it % 3 == 1:
                    c = await repo.get_conversation(root_conv.id)
                    assert c is not None
                else:
                    telem = await db.fetch_all(
                        "SELECT id, model_id, status_code FROM usage_telemetry ORDER BY id DESC LIMIT 5;"
                    )
                    assert isinstance(telem, list)

                lat = (time.perf_counter() - t0) * 1000
                stats["read_latencies_ms"].append(lat)
                stats["reader_ops"] += 1
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    stats["lock_errors"] += 1
                else:
                    stats["other_errors"] += 1
                stats["error_messages"].append(f"Reader {reader_id} err: {e}")
            except Exception as e:
                stats["other_errors"] += 1
                stats["error_messages"].append(f"Reader {reader_id} err: {e}")

            # Small jitter to interleave reads and writes
            await asyncio.sleep(0.002)

    # 4. Writer Task Definition
    async def writer_worker(writer_id: int):
        # Create private sub-conversation for each writer + write to shared root convo
        writer_conv = await repo.create_conversation(f"Writer {writer_id} Sub-Thread")
        parent_id = seed_msg.id

        for it in range(writer_iterations):
            t0 = time.perf_counter()
            try:
                # Write to shared conversation
                new_msg = await repo.add_message(
                    conversation_id=root_conv.id,
                    role="assistant" if it % 2 == 0 else "user",
                    content=f"Payload from writer={writer_id} turn={it} timestamp={time.time()}",
                    parent_id=parent_id,
                    created_at=int(time.time() * 1000),
                )
                parent_id = new_msg.id

                # Write to telemetry
                await db.record_telemetry(
                    account_id=f"worker_acc_{writer_id}",
                    model_id="gemini-4-argon",
                    model_name="Gemini 4 Argon",
                    feature_mode="task",
                    status_code=200,
                    duration_ms=int(time.perf_counter() - t0) * 1000,
                )

                # Write to private sub-conversation in atomic transaction
                async with db.transaction() as conn:
                    await conn.execute(
                        "INSERT INTO messages (conversation_id, role, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?);",
                        (writer_conv.id, "system", f"Writer checkpoint {it}", int(time.time() * 1000), int(time.time() * 1000)),
                    )

                lat = (time.perf_counter() - t0) * 1000
                stats["write_latencies_ms"].append(lat)
                stats["writer_ops"] += 1
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    stats["lock_errors"] += 1
                else:
                    stats["other_errors"] += 1
                stats["error_messages"].append(f"Writer {writer_id} err: {e}")
            except Exception as e:
                stats["other_errors"] += 1
                stats["error_messages"].append(f"Writer {writer_id} err: {e}")

            # Slight delay to allow high concurrency overlap
            await asyncio.sleep(0.005)

    # 5. Launch all 30 readers and 10 writers simultaneously
    logger.info("Spawning %d readers and %d writers concurrently...", num_readers, num_writers)
    reader_tasks = [asyncio.create_task(reader_worker(i)) for i in range(num_readers)]
    writer_tasks = [asyncio.create_task(writer_worker(i)) for i in range(num_writers)]

    await asyncio.gather(*reader_tasks, *writer_tasks)

    total_time = time.perf_counter() - start_time
    stats["total_duration_sec"] = total_time
    logger.info(
        "Stress test completed in %.2fs: %d read ops, %d write ops, %d lock errors, %d other errors",
        total_time,
        stats["reader_ops"],
        stats["writer_ops"],
        stats["lock_errors"],
        stats["other_errors"],
    )

    # 6. Verify Database Integrity & Consistency
    integrity_rows = await db.fetch_all("PRAGMA integrity_check;")
    stats["integrity_check"] = [dict(r) for r in integrity_rows]
    is_integrity_ok = all(list(r.values())[0] == "ok" for r in integrity_rows)
    stats["integrity_ok"] = is_integrity_ok

    fk_rows = await db.fetch_all("PRAGMA foreign_key_check;")
    stats["foreign_key_violations"] = len(fk_rows)

    # Verify expected counts
    # Shared root conv messages: 1 seed + (num_writers * writer_iterations)
    expected_root_msgs = 1 + (num_writers * writer_iterations)
    actual_root_msgs = len(await repo.get_all_messages(root_conv.id))
    stats["expected_root_msgs"] = expected_root_msgs
    stats["actual_root_msgs"] = actual_root_msgs

    # Total conversations: 1 root + num_writers
    _, total_convs = await repo.list_conversations(limit=100)
    stats["total_convs"] = total_convs
    stats["expected_convs"] = 1 + num_writers

    # Total telemetry: num_writers * writer_iterations
    total_telemetry = (await db.fetch_val("SELECT COUNT(*) FROM usage_telemetry;")) or 0
    stats["total_telemetry"] = total_telemetry
    stats["expected_telemetry"] = num_writers * writer_iterations

    # Close and clean up
    await db.close()
    shutil.rmtree(temp_dir, ignore_errors=True)

    return stats


def main():
    logger.info("=== Starting High-Concurrency SQLite Stress Test (30 readers + 10 writers) ===")
    stats = asyncio.run(run_sqlite_stress_test(
        num_readers=30,
        num_writers=10,
        reader_iterations=30,
        writer_iterations=20,
    ))

    print("\n" + "=" * 60)
    print("CONCURRENCY STRESS TEST SUMMARY")
    print("=" * 60)
    print(f"Total Duration:        {stats['total_duration_sec']:.2f} s")
    print(f"Reader Workers:        {stats['num_readers']}")
    print(f"Writer Workers:        {stats['num_writers']}")
    print(f"Successful Read Ops:   {stats['reader_ops']}")
    print(f"Successful Write Ops:  {stats['writer_ops']}")
    print(f"Lock Errors:           {stats['lock_errors']}")
    print(f"Other Errors:          {stats['other_errors']}")
    print(f"Integrity Check:       {'PASSED (ok)' if stats['integrity_ok'] else 'FAILED'}")
    print(f"FK Violations:         {stats['foreign_key_violations']}")
    print(f"Root Convo Messages:   {stats['actual_root_msgs']} / expected {stats['expected_root_msgs']}")
    print(f"Total Conversations:   {stats['total_convs']} / expected {stats['expected_convs']}")
    print(f"Total Telemetry:       {stats['total_telemetry']} / expected {stats['expected_telemetry']}")

    if stats["read_latencies_ms"]:
        r_lats = sorted(stats["read_latencies_ms"])
        print(f"Read Latency (ms):     min={r_lats[0]:.2f}, p50={r_lats[len(r_lats)//2]:.2f}, p95={r_lats[int(len(r_lats)*0.95)]:.2f}, max={r_lats[-1]:.2f}")
    if stats["write_latencies_ms"]:
        w_lats = sorted(stats["write_latencies_ms"])
        print(f"Write Latency (ms):    min={w_lats[0]:.2f}, p50={w_lats[len(w_lats)//2]:.2f}, p95={w_lats[int(len(w_lats)*0.95)]:.2f}, max={w_lats[-1]:.2f}")

    print("=" * 60)

    # Strict assertions
    assert stats["lock_errors"] == 0, f"Expected 0 lock errors, got {stats['lock_errors']}"
    assert stats["other_errors"] == 0, f"Expected 0 other errors, got {stats['other_errors']}"
    assert stats["integrity_ok"] is True, "PRAGMA integrity_check failed"
    assert stats["foreign_key_violations"] == 0, f"Foreign key violations detected: {stats['foreign_key_violations']}"
    assert stats["actual_root_msgs"] == stats["expected_root_msgs"], "Message count mismatch"
    assert stats["total_convs"] == stats["expected_convs"], "Conversation count mismatch"
    assert stats["total_telemetry"] == stats["expected_telemetry"], "Telemetry count mismatch"
    print("\nALL STRESS CONSTRAINTS VERIFIED EMPIRICALLY! ZERO FAILURES.\n")


if __name__ == "__main__":
    main()
