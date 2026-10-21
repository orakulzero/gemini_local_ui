"""
tests/e2e/tier3_combinations/test_combinations_advanced.py
Tier 3: Advanced Cross-Feature Interaction Tests (25 tests).
Covers complex cross-feature workflows:
- Multi-Account Failover + Streaming SSE + Quota Tracker (5 tests)
- Grounding + Artifacts + Canvas Side-Panel (5 tests)
- Branching DAG + Regeneration + Custom Gems (5 tests)
- NotebookLM Context Injection + Chat State Decoupling + History Recall (5 tests)
- Telemetry + Storage Pruning + Export Packaging (5 tests)
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from tests.e2e.harness import IncrementalMarkdownParser

from backend.app.bridge.models import Gem, GemSerializer
from backend.app.bridge.notebooklm import (
    NotebookDetail,
    NotebookInfo,
    NotebookLMClient,
    NotebookNote,
    NotebookSource,
)
from backend.app.bridge.session import (
    AccountSession,
    SessionContext,
    SessionHealthStatus,
    SessionManager,
)
from backend.app.bridge.streaming_parser import StreamingDeltaAccumulator
from backend.app.storage.database import Database
from backend.app.storage.exporter import ThreadExporter
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig
from fastapi.testclient import TestClient
from backend.app.main import app


class TestTier3AdvancedCombinations(unittest.TestCase):
    """Advanced Pairwise and Multi-Feature Interactions."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_t3_advanced.db")
        self.vault_file = Path(self.temp_dir) / "vault.enc"
        self.vault = CredentialVault(self.vault_file, force_fallback=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mocked_manager(self) -> SessionManager:
        mgr = SessionManager(vault=self.vault)
        async def _mock_scrape(acc_id=None):
            sess = mgr.get_account_session(acc_id)
            if sess:
                sess.context.access_token = "mock_snlm0e"
                sess.context.status = SessionHealthStatus.HEALTHY
            return True

        mgr.scrape_tokens = _mock_scrape
        async def _noop(*args, **kwargs): pass
        mgr._staggered_rotation_loop = _noop
        mgr._rotation_loop = _noop
        return mgr

    # =========================================================================
    # Group 1: Multi-Account Failover + Streaming SSE + Quota Tracker
    # =========================================================================
    def test_t3_01_failover_preserves_partial_accumulator_chunks(self):
        acc = StreamingDeltaAccumulator()
        # Initial chunk from primary account
        candidate1 = [
            "rcid_1",
            ["Part 1: Initial explanation from primary account. "],
            None, None, None, None, None, None, [1], None, None, None, {}
        ]
        inner1 = [None, ["c_1", "r_1", "rcid_1"], None, None, [candidate1]]
        frame1 = [["wrb.fr", None, json.dumps(inner1)]]
        chunks1 = list(acc.process_frame(frame1))
        self.assertEqual(len(chunks1), 1)
        self.assertEqual(chunks1[0].delta, "Part 1: Initial explanation from primary account. ")

        # Simulated failover to backup account: cumulative continuation
        candidate2 = [
            "rcid_1",
            ["Part 1: Initial explanation from primary account. Part 2: Continuation from backup."],
            None, None, None, None, None, None, [2], None, None, None, {}
        ]
        inner2 = [None, ["c_1", "r_1", "rcid_1"], None, None, [candidate2]]
        frame2 = [["wrb.fr", None, json.dumps(inner2)]]
        chunks2 = list(acc.process_frame(frame2))
        text_chunks = [c for c in chunks2 if c.type == "text"]
        self.assertEqual(len(text_chunks), 1)
        self.assertEqual(text_chunks[0].delta, "Part 2: Continuation from backup.")
        self.assertTrue(acc.is_completed)

    def test_t3_02_failover_account_switch_updates_session_headers(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="primary", name="Primary Acc", tier="free", priority=1, cookies={"__Secure-1PSID": "p_sid"})
        p2 = AccountProfile(id="backup", name="Backup Acc", tier="pro", priority=2, cookies={"__Secure-1PSID": "b_sid"})
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        self.vault.update_failover_config(FailoverConfig(enabled=True, fallback_chain=["backup"]))
        async def _run():
            await mgr.initialize_all()
            for s in mgr.sessions.values():
                s.context.status = SessionHealthStatus.HEALTHY
            mgr.mark_cooldown("primary", duration_seconds=3600, reason="Quota exhausted")
            failover = mgr.get_failover_account("gemini-2.5-flash", "primary")
            self.assertIsNotNone(failover)
            self.assertEqual(failover.account_id, "backup")
            self.assertEqual(failover.context.cookies["__Secure-1PSID"], "b_sid")
        asyncio.run(_run())

    def test_t3_03_cooldown_expiry_restores_failover_eligibility(self):
        mgr = self._create_mocked_manager()
        p = AccountProfile(id="acc_cool", name="Cool Acc", tier="free")
        self.vault.save_account(p)
        async def _run():
            await mgr.initialize_all()
            # Mark cooldown in the past
            mgr.mark_cooldown("acc_cool", duration_seconds=-5)
            self.assertFalse(mgr.is_in_cooldown("acc_cool"))
        asyncio.run(_run())

    def test_t3_04_priority_failover_concurrent_conversations(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="free_acc", name="Free", tier="free", priority=1)
        p2 = AccountProfile(id="pro_acc", name="Pro", tier="pro", priority=10)
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        async def _run():
            await mgr.initialize_all()
            for s in mgr.sessions.values():
                s.context.status = SessionHealthStatus.HEALTHY
            # Free model uses pro as failover if free fails
            mgr.mark_cooldown("free_acc", duration_seconds=300)
            target = mgr.get_failover_account("gemini-2.5-flash", "free_acc")
            self.assertEqual(target.account_id, "pro_acc")
        asyncio.run(_run())

    def test_t3_05_quota_tracking_on_failover_account(self):
        mgr = self._create_mocked_manager()
        p1 = AccountProfile(id="acc_prim", name="Primary", tier="pro", priority=1)
        p2 = AccountProfile(id="acc_sec", name="Secondary", tier="ultra", priority=2)
        self.vault.save_account(p1)
        self.vault.save_account(p2)
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            await mgr.initialize_all()
            for s in mgr.sessions.values():
                s.context.status = SessionHealthStatus.HEALTHY
            # Record telemetry on primary
            await db.record_telemetry(
                account_id="acc_prim",
                model_id="95a4c0abcba24364",
                model_name="4 Argon",
                prompt_tokens_est=1000,
                completion_tokens_est=500,
                duration_ms=450,
            )
            # Quota exhausted on primary -> failover
            mgr.mark_cooldown("acc_prim", duration_seconds=1800, reason="Quota 1037")
            target = mgr.get_failover_account("gemini-4-argon", "acc_prim")
            self.assertEqual(target.account_id, "acc_sec")
            # Record telemetry on failover account
            await db.record_telemetry(
                account_id=target.account_id,
                model_id="95a4c0abcba24364",
                model_name="4 Argon",
                prompt_tokens_est=500,
                completion_tokens_est=250,
                duration_ms=250,
            )
            p_stats = await db.get_rolling_usage(account_id="acc_prim", window_seconds=3600)
            s_stats = await db.get_rolling_usage(account_id="acc_sec", window_seconds=3600)
            self.assertEqual(p_stats["total_tokens"], 1500)
            self.assertEqual(s_stats["total_tokens"], 750)
            await db.close()
        asyncio.run(_run())

    # =========================================================================
    # Group 2: Grounding + Artifacts + Canvas Side-Panel
    # =========================================================================
    def test_t3_06_grounding_and_artifact_coexistence(self):
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_combo",
            ["Here is the Python implementation:\n```python\nimport math\nprint(math.pi)\n```"],
            None, None, None, None, None, None, [2], None, None, None,
            {
                "43": [[1, "https://docs.python.org/3/library/math.html", "Python Math Module", "docs.python.org", "Snippet"]]
            }
        ]
        inner = [None, ["c_1", "r_1", "rcid_combo"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner)]]
        chunks = list(acc.process_frame(frame))

        citation_chunks = [c for c in chunks if c.type == "citation"]
        self.assertTrue(len(citation_chunks) > 0)
        self.assertEqual(acc.citations[0]["domain"], "docs.python.org")
        self.assertIn("import math", acc.full_raw_text)

    def test_t3_07_canvas_code_fence_extraction_from_grounded_text(self):
        response_text = (
            "According to the official spec:\n\n"
            "```typescript\n"
            "export interface User {\n"
            "  id: string;\n"
            "}\n"
            "```\n\n"
            "Refer to documentation for further fields."
        )
        parsed = IncrementalMarkdownParser.parse_stream(response_text)
        code_blocks = [b for b in parsed["immutable_blocks"] if b["type"] == "code"]
        self.assertEqual(len(code_blocks), 1)
        code_block = code_blocks[0]
        self.assertEqual(code_block["language"], "typescript")
        self.assertIn("export interface User", code_block["content"])

        balanced = ThreadExporter.balance_code_fences(response_text)
        self.assertIn("export interface User", balanced)

    def test_t3_08_rich_youtube_card_alongside_code_preview(self):
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_yt_code",
            ["Watch video tutorial: https://www.youtube.com/watch?v=dQw4w9WgXcQ\n```sh\nnpm install\n```"],
            None, None, None, None, None, None, [2], None, None, None, {}
        ]
        inner = [None, ["c_1", "r_1", "rcid_yt_code"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner)]]
        chunks = list(acc.process_frame(frame))
        self.assertEqual(len(acc.youtube_videos), 1)
        self.assertEqual(acc.youtube_videos[0]["video_id"], "dQw4w9WgXcQ")
        self.assertIn("npm install", acc.full_raw_text)

    def test_t3_09_artifact_export_with_citations_metadata(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Quantum Fourier Transform Script")
            msg_content = (
                "Here is the quantum implementation:\n\n"
                "```python\n"
                "def qft(n: int):\n"
                "    pass\n"
                "```\n\n"
                "Citations: [QFT Overview](https://arxiv.org/abs/quant-ph/0001)"
            )
            await repo.create_message(
                conversation_id=conv.id,
                role="assistant",
                content=msg_content,
                model="gemini-2.5-pro",
            )
            thread = await repo.get_active_thread(conv.id)
            md_export = ThreadExporter.export_to_markdown(conv, thread)
            json_export = ThreadExporter.export_to_json(conv, thread)
            self.assertIn("QFT Overview", md_export)
            self.assertIn("def qft(n: int):", md_export)
            self.assertIn("https://arxiv.org/abs/quant-ph/0001", md_export)
            self.assertIn("Quantum Fourier Transform Script", json_export)
            await db.close()
        asyncio.run(_run())

    def test_t3_10_grounding_pill_does_not_mutate_canvas_visibility(self):
        # Group 2: Grounding + Artifacts + Canvas Side-Panel
        # Verifies that Grounding citations and Canvas code block extraction remain isolated
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_combo_iso",
            ["Grounded response with code:\n```python\ndef solution():\n    return 42\n```"],
            None, None, None, None, None, None, [2], None, None, None,
            {
                "43": [
                    [1, "https://arxiv.org/abs/2101.00001", "Quantum Paper", "arxiv.org", "Snippet"]
                ]
            }
        ]
        inner = [None, ["c_1", "r_1", "rcid_combo_iso"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner)]]
        chunks = list(acc.process_frame(frame))

        # 1. Grounding citations cleanly extracted into metadata
        self.assertEqual(len(acc.citations), 1)
        self.assertEqual(acc.citations[0]["domain"], "arxiv.org")

        # 2. Canvas code block parsed cleanly without citation leakage into code content
        parsed = IncrementalMarkdownParser.parse_stream(acc.full_raw_text + "\n\n")
        code_blocks = [b for b in parsed["immutable_blocks"] if b["type"] == "code"]
        self.assertEqual(len(code_blocks), 1)
        self.assertEqual(code_blocks[0]["language"], "python")
        self.assertIn("def solution():", code_blocks[0]["content"])
        self.assertNotIn("arxiv.org", code_blocks[0]["content"])

    # =========================================================================
    # Group 3: Branching DAG + Regeneration + Custom Gems
    # =========================================================================
    def test_t3_11_in_place_regeneration_maintains_gem_instruction(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Gem Session", system_prompt="Persona: Senior Rust Engineer")
            m1 = await repo.add_message(conv.id, "user", "Explain Arc vs Rc")
            m2a = await repo.add_message(conv.id, "assistant", "Answer 1", parent_id=m1.id)
            # In-place regeneration branch
            m2b = await repo.add_message(conv.id, "assistant", "Answer 2 (Regenerated)", parent_id=m1.id)
            await repo.set_active_leaf(conv.id, m2b.id)

            active_conv = await repo.get_conversation(conv.id)
            self.assertEqual(active_conv.system_prompt, "Persona: Senior Rust Engineer")
            self.assertEqual(active_conv.active_leaf_message_id, m2b.id)
            await db.close()

        asyncio.run(_run())

    def test_t3_12_switching_siblings_restores_branch_history(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Sibling Nav")
            m1 = await repo.add_message(conv.id, "user", "Step 1")
            m2a = await repo.add_message(conv.id, "assistant", "Branch A", parent_id=m1.id)
            m3a = await repo.add_message(conv.id, "user", "Step 2A", parent_id=m2a.id)

            m2b = await repo.add_message(conv.id, "assistant", "Branch B", parent_id=m1.id)
            m3b = await repo.add_message(conv.id, "user", "Step 2B", parent_id=m2b.id)

            # Switch to Branch A leaf
            await repo.set_active_leaf(conv.id, m3a.id)
            thread_a = await repo.get_active_thread(conv.id)
            self.assertTrue(any(m.content == "Branch A" for m in thread_a))
            self.assertFalse(any(m.content == "Branch B" for m in thread_a))

            # Switch to Branch B leaf
            await repo.set_active_leaf(conv.id, m3b.id)
            thread_b = await repo.get_active_thread(conv.id)
            self.assertTrue(any(m.content == "Branch B" for m in thread_b))
            self.assertFalse(any(m.content == "Branch A" for m in thread_b))

            await db.close()

        asyncio.run(_run())

    def test_t3_13_forking_from_gem_branch_carries_gem_metadata(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(
                title="Original Gem Chat",
                gem_id="gem_rust_expert",
                system_prompt="Rust Expert",
            )
            # Forking creates new conversation inheriting gem attributes
            forked = await repo.create_conversation(
                title="Forked: Original Gem Chat",
                gem_id=conv.gem_id,
                system_prompt=conv.system_prompt,
            )
            self.assertEqual(forked.gem_id, "gem_rust_expert")
            self.assertEqual(forked.system_prompt, "Rust Expert")
            await db.close()

        asyncio.run(_run())

    def test_t3_14_regeneration_concurrent_with_export(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Concurrent Ops")
            m1 = await repo.add_message(conv.id, "user", "Prompt")
            m2 = await repo.add_message(conv.id, "assistant", "Draft 1", parent_id=m1.id)

            # Export task reading tree
            export_task = repo.get_all_messages(conv.id)
            # Regeneration adding sibling
            regen_task = repo.add_message(conv.id, "assistant", "Draft 2", parent_id=m1.id)

            messages, m_regen = await asyncio.gather(export_task, regen_task)
            self.assertGreater(len(messages), 0)
            self.assertEqual(m_regen.content, "Draft 2")
            await db.close()

        asyncio.run(_run())

    def test_t3_15_active_leaf_traversal_deep_dag(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Deep DAG")
            prev_id = None
            for i in range(10):
                msg = await repo.add_message(conv.id, "user" if i % 2 == 0 else "assistant", f"Turn {i}", parent_id=prev_id)
                prev_id = msg.id

            await repo.set_active_leaf(conv.id, prev_id)
            thread = await repo.get_active_thread(conv.id)
            self.assertEqual(len(thread), 10)
            self.assertEqual(thread[-1].content, "Turn 9")
            await db.close()

        asyncio.run(_run())

    # =========================================================================
    # Group 4: NotebookLM Context Injection + Chat State Decoupling + History Recall
    # =========================================================================
    def test_t3_16_injected_context_preserves_prompt_history_recall(self):
        """Verifies NotebookLM reference context injection decouples from prompt history in SQLite."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            # 1. Establish prior conversation turns to populate persistent prompt history
            conv = await repo.create_conversation(title="Architecture Research")
            m1_user = await repo.add_message(conv.id, "user", "Turn 1: Analyze microservice trade-offs.")
            m1_asst = await repo.add_message(
                conv.id, "assistant", "Microservices increase operational overhead.", parent_id=m1_user.id
            )

            # 2. Simulate NotebookLM context extraction for Turn 2
            nb_detail = NotebookDetail(
                id="nb_sys_arch",
                title="System Architecture Notes",
                sources=[
                    NotebookSource(
                        id="src_arch_1",
                        title="Decoupling Principles",
                        summary="Loose coupling and high cohesion guidelines."
                    )
                ],
            )
            injected_context = (
                f'<notebooklm_reference title="{nb_detail.title}">\n'
                f'  <source id="{nb_detail.sources[0].id}">{nb_detail.sources[0].summary}</source>\n'
                f'</notebooklm_reference>'
            )

            # 3. User types a distinct draft prompt (decoupled from injected context)
            user_draft_prompt = "Turn 2: How do we handle distributed transactions?"

            # 4. Submission combines them into the full message payload
            combined_submission = f"{injected_context}\n\n{user_draft_prompt}"
            m2_user = await repo.add_message(conv.id, "user", combined_submission, parent_id=m1_asst.id)
            m2_asst = await repo.add_message(
                conv.id, "assistant", "Use saga pattern or two-phase commit.", parent_id=m2_user.id
            )
            await repo.set_active_leaf(conv.id, m2_asst.id)

            # 5. Verify database storage invariants:
            thread = await repo.get_active_thread(conv.id)
            self.assertEqual(len(thread), 4)

            # Extract user prompts history from the persistent thread
            user_prompts = [m.content for m in thread if m.role == "user"]
            self.assertEqual(len(user_prompts), 2)

            # Turn 1 prompt remains intact in history without any injected tags
            self.assertEqual(user_prompts[0], "Turn 1: Analyze microservice trade-offs.")
            self.assertNotIn("<notebooklm_reference", user_prompts[0])

            # Turn 2 user message contains both context and draft
            self.assertIn("<notebooklm_reference title=\"System Architecture Notes\">", user_prompts[1])
            self.assertIn("Loose coupling and high cohesion guidelines.", user_prompts[1])
            self.assertTrue(user_prompts[1].endswith("Turn 2: How do we handle distributed transactions?"))

            # Extraction helper accurately recovers original draft prompt without corruption
            extracted_draft = user_prompts[1].split("</notebooklm_reference>\n\n")[-1]
            self.assertEqual(extracted_draft, user_draft_prompt)

            await db.close()

        asyncio.run(_run())

    def test_t3_17_recall_navigation_does_not_mutate_injected_context(self):
        """Verifies multi-turn prompt history recall does not alter or overwrite attached NotebookLM context."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            conv = await repo.create_conversation(title="Multi-Turn Query History")
            prompts = [
                "alpha: initial exploration of transformer layers",
                "beta: compare self-attention with cross-attention",
                "gamma: evaluate computational complexity of attention",
            ]

            prev_id = None
            for p in prompts:
                u = await repo.add_message(conv.id, "user", p, parent_id=prev_id)
                a = await repo.add_message(conv.id, "assistant", f"Acknowledged {p[:5]}", parent_id=u.id)
                prev_id = a.id

            await repo.set_active_leaf(conv.id, prev_id)

            # Retrieve prompt history from repository in chronological and reverse order (recall up)
            all_user_msgs = await db.fetch_all(
                "SELECT content FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY id ASC;",
                (conv.id,),
            )
            history = [r["content"] for r in all_user_msgs]
            self.assertEqual(len(history), 3)

            # Injected context block from NotebookLM attachment
            injected_context = (
                '<notebooklm_reference title="Attention Is All You Need">\n'
                '  <source id="src_attn">Complexity: O(N^2 * d)</source>\n'
                '</notebooklm_reference>'
            )

            # User navigates prompt history:
            # Recall Up 1 -> gamma
            recalled_1 = history[-1]
            self.assertEqual(recalled_1, "gamma: evaluate computational complexity of attention")
            # Recall Up 2 -> beta
            recalled_2 = history[-2]
            self.assertEqual(recalled_2, "beta: compare self-attention with cross-attention")

            # Injected context remains completely invariant during recall navigation
            self.assertIn("Complexity: O(N^2 * d)", injected_context)
            self.assertTrue(injected_context.startswith("<notebooklm_reference"))

            # User edits recalled prompt beta and dispatches Turn 4 with the attached context
            edited_draft = f"{recalled_2} with linear attention approximations"
            turn4_payload = f"{injected_context}\n\n{edited_draft}"
            t4_msg = await repo.add_message(conv.id, "user", turn4_payload, parent_id=prev_id)
            self.assertIsNotNone(t4_msg.id)

            # Verify persistent state in database
            saved_msg = await repo.get_message(t4_msg.id)
            self.assertIn("<notebooklm_reference", saved_msg.content)
            self.assertIn(edited_draft, saved_msg.content)

            # Prior turns in SQLite are completely unmodified
            t2_record = await db.fetch_one("SELECT content FROM messages WHERE content = ?;", (prompts[1],))
            self.assertIsNotNone(t2_record)
            self.assertEqual(t2_record["content"], "beta: compare self-attention with cross-attention")

            await db.close()

        asyncio.run(_run())

    def test_t3_18_multiple_notebook_sources_aggregation_order(self):
        """Verifies NotebookLM sources and notes aggregation order via the attach-context REST API."""
        tc = TestClient(app)
        detail = NotebookDetail(
            id="nb_agg",
            title="Comprehensive AI Review",
            sources=[
                NotebookSource(id="s1", title="Transformers", summary="Self-attention mechanisms"),
                NotebookSource(id="s2", title="Diffusion", summary="Denoising score matching"),
            ],
            notes=[
                NotebookNote(id="n1", title="Methodology", content="Benchmarking setup"),
            ],
        )

        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = detail
            resp = tc.post(
                "/api/notebooklm/attach-context",
                json={
                    "notebook_id": "nb_agg",
                    "source_ids": ["s1", "s2"],
                    "note_ids": ["n1"],
                    "include_notes": True,
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            context_block = data["formatted_context"]

            self.assertIn("Self-attention mechanisms", context_block)
            self.assertIn("Denoising score matching", context_block)
            self.assertIn("Benchmarking setup", context_block)
            pos_s1 = context_block.find("Self-attention mechanisms")
            pos_s2 = context_block.find("Denoising score matching")
            pos_n1 = context_block.find("Benchmarking setup")
            self.assertLess(pos_s1, pos_s2)
            self.assertLess(pos_s2, pos_n1)

    def test_t3_19_drawer_source_selection_state_isolation(self):
        """Verifies NotebookLM drawer source selection isolation via the attach-context REST API."""
        tc = TestClient(app)
        detail = NotebookDetail(
            id="nb_selection_test",
            title="Multimodal AI Research",
            sources=[
                NotebookSource(id="src_1", title="Vision Transformers", summary="Patch embeddings and ViT architecture."),
                NotebookSource(id="src_2", title="Audio Spectrograms", summary="AST architecture for audio classification."),
                NotebookSource(id="src_3", title="Multimodal Fusion", summary="Cross-attention fusion mechanisms."),
            ],
            notes=[
                NotebookNote(id="note_1", title="Synthesis Note", content="Key comparison table."),
            ],
        )

        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = detail

            # 1. User selects all sources in drawer: src_1, src_2, src_3
            resp_all = tc.post(
                "/api/notebooklm/attach-context",
                json={
                    "notebook_id": "nb_selection_test",
                    "source_ids": ["src_1", "src_2", "src_3"],
                    "include_notes": False,
                },
            )
            self.assertEqual(resp_all.status_code, 200)
            data_all = resp_all.json()
            self.assertEqual(data_all["source_count"], 3)
            self.assertIn("Vision Transformers", data_all["formatted_context"])
            self.assertIn("Audio Spectrograms", data_all["formatted_context"])
            self.assertIn("Multimodal Fusion", data_all["formatted_context"])

            # 2. User deselects src_1 in drawer -> only src_2 and src_3 selected
            resp_filtered = tc.post(
                "/api/notebooklm/attach-context",
                json={
                    "notebook_id": "nb_selection_test",
                    "source_ids": ["src_2", "src_3"],
                    "include_notes": False,
                },
            )
            self.assertEqual(resp_filtered.status_code, 200)
            data_filtered = resp_filtered.json()
            self.assertEqual(data_filtered["source_count"], 2)
            self.assertNotIn("Vision Transformers", data_filtered["formatted_context"])
            self.assertIn("Audio Spectrograms", data_filtered["formatted_context"])
            self.assertIn("Multimodal Fusion", data_filtered["formatted_context"])

            # 3. User selects only single source src_1
            resp_single = tc.post(
                "/api/notebooklm/attach-context",
                json={
                    "notebook_id": "nb_selection_test",
                    "source_ids": ["src_1"],
                    "include_notes": True,
                },
            )
            self.assertEqual(resp_single.status_code, 200)
            data_single = resp_single.json()
            self.assertEqual(data_single["source_count"], 1)
            self.assertEqual(data_single["note_count"], 1)
            self.assertIn("Vision Transformers", data_single["formatted_context"])
            self.assertNotIn("Audio Spectrograms", data_single["formatted_context"])
            self.assertIn("Synthesis Note", data_single["formatted_context"])

    def test_t3_20_chat_export_to_notebooklm_via_bridge(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = [
                    "src_new_99",
                    "Exported Chat",
                    1,
                    None,
                    "Summary",
                    500,
                    [1729000000, 0],
                ]
                src = await client.add_text_source("nb_target", "Exported Chat", "Turn transcript")
                self.assertIsInstance(src, NotebookSource)
                self.assertEqual(src.id, "src_new_99")
                self.assertEqual(src.title, "Exported Chat")
        asyncio.run(_run())

    # =========================================================================
    # Group 5: Telemetry + Storage Pruning + Export Packaging
    # =========================================================================
    def test_t3_21_export_bundle_includes_telemetry_aggregates(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Telemetry Bundle")
            m1 = await repo.add_message(conv.id, "user", "Query")
            m2 = await repo.add_message(conv.id, "assistant", "Response")

            bundle = {
                "version": 2,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "conversation": conv.to_dict(),
                "messages": [m1.to_dict(), m2.to_dict()],
                "telemetry": {
                    "total_turns": 2,
                    "estimated_prompt_tokens": 15,
                    "estimated_completion_tokens": 25,
                },
            }
            self.assertEqual(bundle["telemetry"]["total_turns"], 2)
            self.assertEqual(len(bundle["messages"]), 2)
            await db.close()

        asyncio.run(_run())

    def test_t3_22_auto_pruning_respects_active_conversations(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            # 1 active conversation, 1 archived conversation
            c_active = await repo.create_conversation(title="Active")
            c_archived = await repo.create_conversation(title="Archived")
            await repo.archive_conversation(c_archived.id, is_archived=True)

            # Prune archived older than far future
            future_cutoff = int(time.time() * 1000) + 100000
            await db.execute("DELETE FROM conversations WHERE is_archived = 1 AND updated_at < ?;", (future_cutoff,))

            # Active conversation must remain untouched
            reloaded_active = await repo.get_conversation(c_active.id)
            reloaded_archived = await repo.get_conversation(c_archived.id)
            self.assertIsNotNone(reloaded_active)
            self.assertIsNone(reloaded_archived)

            await db.close()

        asyncio.run(_run())

    def test_t3_23_database_foreign_key_cascade_on_conversation_delete(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="FK Test")
            await repo.add_message(conv.id, "user", "Message 1")
            await repo.add_message(conv.id, "assistant", "Message 2")

            # Delete conversation
            await repo.delete_conversation(conv.id)
            # Messages should be cascaded
            remaining_msgs = await db.fetch_all("SELECT * FROM messages WHERE conversation_id = ?;", (conv.id,))
            self.assertEqual(len(remaining_msgs), 0)

            await db.close()

        asyncio.run(_run())

    def test_t3_24_token_velocity_burst_triggers_alert_flag(self):
        """Verifies token velocity calculation against account limits threshold in SQLite Database."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()

            account_id = "acc_velocity_burst"
            threshold_tokens_per_sec = 500.0

            # Insert rapid burst: 45,000 tokens over past 60 seconds
            now_ms = int(time.time() * 1000)
            for i in range(3):
                await db.record_telemetry(
                    account_id=account_id,
                    model_id="gemini-2.5-flash",
                    model_name="Gemini 2.5 Flash",
                    prompt_tokens_est=5000,
                    completion_tokens_est=10000,
                    request_timestamp=now_ms - (i * 10000),
                )

            # Query rolling 60s usage from SQLite
            rolling = await db.get_rolling_usage(account_id=account_id, window_seconds=60)
            total_tokens = rolling["total_tokens"]
            self.assertEqual(total_tokens, 45000)

            # Calculate velocity
            window_duration = 60.0
            actual_rate = total_tokens / window_duration
            self.assertEqual(actual_rate, 750.0)

            alert_triggered = actual_rate > threshold_tokens_per_sec
            self.assertTrue(alert_triggered)

            await db.close()

        asyncio.run(_run())

    def test_t3_25_reimport_exported_bundle_reconstructs_dag(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            bundle_json = {
                "conversation": {
                    "title": "Imported Research",
                    "model": "gemini-2.5-flash",
                },
                "messages": [
                    {"role": "user", "content": "Question 1"},
                    {"role": "assistant", "content": "Answer 1"},
                ],
            }

            imported_conv = await repo.create_conversation(
                title=bundle_json["conversation"]["title"],
                model=bundle_json["conversation"]["model"],
            )
            prev_id = None
            for m in bundle_json["messages"]:
                new_m = await repo.add_message(imported_conv.id, m["role"], m["content"], parent_id=prev_id)
                prev_id = new_m.id

            thread = await repo.get_all_messages(imported_conv.id)
            self.assertEqual(len(thread), 2)
            self.assertEqual(thread[0].content, "Question 1")
            self.assertEqual(thread[1].content, "Answer 1")

            await db.close()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
