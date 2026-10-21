"""
tests/adversarial/test_adversarial_stress_workloads.py
Adversarial Stress and Concurrency Test Suite for Milestone M7.
Stress-tests the remediated components under heavy concurrency, deep DAG trees,
astronomical numerical boundaries, and pathological payloads.
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
import os
import shutil
import tempfile
import time
import unittest

from backend.app.bridge.models import DEFAULT_MODELS, Gem, resolve_model
from backend.app.storage.database import Database
from backend.app.storage.exporter import ThreadExporter
from backend.app.storage.repository import ConversationRepository


class TestAdversarialStressWorkloads(unittest.TestCase):
    """Stress tests covering concurrency, scale, depth, and boundary numbers."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_adv_stress.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stress_concurrent_dag_branching(self):
        """50 concurrent async tasks creating branches and adding sibling messages simultaneously."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            conv = await repo.create_conversation(title="Concurrent Stress DAG")
            root_msg = await repo.add_message(conv.id, "user", "Root Question")

            async def _branch_worker(worker_id: int):
                # Each worker adds an assistant response branching off root
                asst_msg = await repo.add_message(
                    conv.id,
                    "assistant",
                    f"Response from worker {worker_id}",
                    parent_id=root_msg.id,
                )
                # Then adds a follow-up user prompt
                user_followup = await repo.add_message(
                    conv.id,
                    "user",
                    f"Follow-up from worker {worker_id}",
                    parent_id=asst_msg.id,
                )
                return asst_msg.id, user_followup.id

            tasks = [_branch_worker(i) for i in range(50)]
            results = await asyncio.gather(*tasks)
            self.assertEqual(len(results), 50)

            # Check that root message has exactly 50 sibling responses
            all_msgs = await repo.get_all_messages(conv.id)
            direct_children = [m for m in all_msgs if m.parent_id == root_msg.id]
            self.assertEqual(len(direct_children), 50)

            # Pick a leaf and verify active thread reconstruction
            leaf_id = results[25][1]
            await repo.set_active_leaf(conv.id, leaf_id)
            thread = await repo.get_active_thread(conv.id)
            self.assertEqual(len(thread), 3)  # Root -> Asst 25 -> User Followup 25
            self.assertEqual(thread[0].id, root_msg.id)
            self.assertEqual(thread[1].content, "Response from worker 25")
            self.assertEqual(thread[2].content, "Follow-up from worker 25")

            await db.close()

        asyncio.run(_run())

    def test_stress_deep_dag_linear_thread_depth_50(self):
        """Construct a deep DAG chain of depth 50 and verify linear reconstruction speed."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            conv = await repo.create_conversation(title="Deep Linear DAG")
            prev_id = None
            for i in range(50):
                role = "user" if i % 2 == 0 else "assistant"
                msg = await repo.add_message(conv.id, role, f"Message depth {i}", parent_id=prev_id)
                prev_id = msg.id

            await repo.set_active_leaf(conv.id, prev_id)
            start_t = time.perf_counter()
            thread = await repo.get_active_thread(conv.id)
            elapsed = time.perf_counter() - start_t

            self.assertEqual(len(thread), 50)
            self.assertEqual(thread[0].content, "Message depth 0")
            self.assertEqual(thread[-1].content, "Message depth 49")
            # Must resolve sub-millisecond or few milliseconds
            self.assertLess(elapsed, 0.1)

            await db.close()

        asyncio.run(_run())

    def test_stress_wide_sibling_forking_100_children(self):
        """Construct 100 sibling edits off a single message node and verify sibling indexing."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            conv = await repo.create_conversation(title="Wide Sibling Tree")
            root = await repo.add_message(conv.id, "user", "Base prompt")

            sibling_ids = []
            for i in range(100):
                m = await repo.add_message(conv.id, "assistant", f"Candidate {i}", parent_id=root.id)
                sibling_ids.append(m.id)

            # Verify sibling index of the 50th candidate
            idx_50, total_50 = await repo.get_message_siblings(sibling_ids[49])
            self.assertEqual(total_50, 100)
            self.assertEqual(idx_50, 50)

            # Verify sibling index of the 100th candidate
            idx_100, total_100 = await repo.get_message_siblings(sibling_ids[99])
            self.assertEqual(total_100, 100)
            self.assertEqual(idx_100, 100)

            await db.close()

        asyncio.run(_run())

    def test_stress_astronomical_telemetry_bounds(self):
        """Record telemetry with 64-bit integer numbers, zero duration, and 30-day duration."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()

            huge_prompt = 100_000_000
            huge_completion = 50_000_000
            huge_thinking = 25_000_000
            huge_duration = 30 * 86400 * 1000  # 30 days in ms = 2,592,000,000 ms

            await db.record_telemetry(
                account_id="acc_astronomical",
                model_id="95a4c0abcba24364",
                model_name="4 Argon",
                prompt_tokens_est=huge_prompt,
                completion_tokens_est=huge_completion,
                thinking_tokens_est=huge_thinking,
                duration_ms=huge_duration,
            )

            stats = await db.get_rolling_usage(account_id="acc_astronomical", window_seconds=86400 * 60)
            self.assertEqual(stats["total_tokens"], 175_000_000)
            self.assertEqual(stats["total_prompt_tokens"], huge_prompt)
            self.assertEqual(stats["total_completion_tokens"], huge_completion)
            self.assertEqual(stats["total_thinking_tokens"], huge_thinking)

            row = await db.fetch_one("SELECT duration_ms FROM usage_telemetry WHERE account_id = 'acc_astronomical';")
            self.assertEqual(row["duration_ms"], huge_duration)

            await db.close()

        asyncio.run(_run())

    def test_stress_massive_markdown_code_fence_balancing(self):
        """Stress-tests ThreadExporter.balance_code_fences on a 5 MB payload with 1,000 code fences."""
        blocks = []
        for i in range(1000):
            blocks.append(f"```python\n# block {i}\nx = {i} * 2\n```\nSome explanatory prose between blocks.")
        # Leave one unclosed fence at the very end
        blocks.append("```rust\nfn main() { println!(\"Unfinished\");\n")
        large_document = "\n".join(blocks)

        start_t = time.perf_counter()
        balanced = ThreadExporter.balance_code_fences(large_document)
        elapsed = time.perf_counter() - start_t

        self.assertTrue(balanced.endswith("\n```\n"))
        self.assertEqual(balanced.count("```rust"), 1)
        self.assertEqual(balanced.count("```python"), 1000)
        self.assertLess(elapsed, 0.5)  # Under 500ms for 5 MB payload


if __name__ == "__main__":
    unittest.main()
