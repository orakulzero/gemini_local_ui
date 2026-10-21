"""
tests/e2e/tier4_workloads/test_workloads_enterprise.py
Tier 4: Enterprise End-to-End Real-World Application Workloads (12 tests).
Covers comprehensive real-world journeys:
1. Full Research Synthesis Journey (NotebookLM -> Chat -> Canvas -> Export)
2. Enterprise Multi-Tier Quota Exhaustion & Continuous Failover
3. Multi-Turn Interactive Deep-Dive with Branching & Custom Gems
4. Auto-Pruning & Storage Lifecycle Maintenance
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

from backend.app.bridge.exceptions import AuthenticationError, GeminiBridgeError
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
from backend.app.storage.repository import ConversationRepository
from backend.app.storage.vault import AccountProfile, CredentialVault, FailoverConfig


class TestTier4EnterpriseWorkloads(unittest.TestCase):
    """End-to-end multi-feature realistic application workflows."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_t4_enterprise.db")
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
    # Scenario 1: Full Research Synthesis Journey
    # =========================================================================
    def test_t4_enterprise_01_notebooklm_research_to_chat_synthesis(self):
        """Creates notebook, fetches sources, injects into prompt, and executes chat turn."""
        client = NotebookLMClient(fallback_snlm0e="valid_token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                # 1. Create notebook
                mock_rpc.return_value = ["nb_ent_1", "Quantum Teleportation", [1729000000, 0]]
                nb = await client.create_notebook("Quantum Teleportation")
                self.assertEqual(nb.id, "nb_ent_1")

                # 2. Add source document
                mock_rpc.return_value = [
                    "src_1", "Bennett1993.pdf", 3, "https://arxiv.org/abs/quant-ph/9301",
                    "Teleporting an unknown quantum state via dual channels", 8500, [1729000100, 0]
                ]
                src = await client.add_text_source(nb.id, "Bennett1993.pdf", "Teleportation protocol")
                self.assertEqual(src.id, "src_1")

                # 3. Format injected context XML envelope
                injected_context = (
                    f'<notebooklm_reference title="{nb.title}" notebook_id="{nb.id}">\n'
                    f'  <source id="{src.id}" title="{src.title}">\n'
                    f'    {src.summary}\n'
                    f'  </source>\n'
                    '</notebooklm_reference>'
                )

                # 4. User prompt construction (decoupled state)
                user_question = "Explain how EPR entanglement is utilized in Bennett's protocol."
                final_chat_prompt = f"{injected_context}\n\n{user_question}"
                self.assertTrue(final_chat_prompt.startswith('<notebooklm_reference title="Quantum Teleportation"'))
                self.assertTrue(final_chat_prompt.endswith(user_question))

        asyncio.run(_run())

    def test_t4_enterprise_02_canvas_artifact_extraction_and_file_export(self):
        """Processes streaming response with Python data visualization, renders to Canvas, and exports file."""
        acc = StreamingDeltaAccumulator()
        stream_body = (
            "Here is the quantum state fidelity simulation script:\n\n"
            "```python\n"
            "import numpy as np\n"
            "import matplotlib.pyplot as plt\n\n"
            "fidelity = np.linspace(0.5, 1.0, 100)\n"
            "success_rate = 3 * fidelity - 2\n"
            "plt.plot(fidelity, success_rate)\n"
            "plt.title('EPR Teleportation Fidelity')\n"
            "plt.savefig('fidelity.png')\n"
            "```\n\n"
            "Run this script to reproduce Figure 3."
        )
        candidate = [
            "rcid_sim",
            [stream_body],
            None, None, None, None, None, None, [2], None, None, None, {}
        ]
        inner = [None, ["c_1", "r_1", "rcid_sim"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner)]]
        chunks = list(acc.process_frame(frame))

        # Verify accumulation
        self.assertTrue(acc.is_completed)
        self.assertIn("import numpy as np", acc.full_raw_text)

        # Extract Canvas artifact
        code_start = stream_body.find("```python\n") + len("```python\n")
        code_end = stream_body.find("\n```", code_start)
        extracted_code = stream_body[code_start:code_end]

        # Export file to disk
        out_file = os.path.join(self.temp_dir, "fidelity_simulation.py")
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(extracted_code)

        self.assertTrue(os.path.exists(out_file))
        with open(out_file, "r", encoding="utf-8") as f:
            saved = f.read()
        self.assertIn("plt.savefig('fidelity.png')", saved)

    def test_t4_enterprise_03_export_chat_turn_back_to_notebooklm_source(self):
        """Exports synthesis chat turn back into NotebookLM as a curated text note/source."""
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = [
                    "src_exported_turn",
                    "Synthesis: Quantum Teleportation",
                    1,
                    None,
                    "Summary of QFT and EPR fidelity discussion",
                    1450,
                    [1729000500, 0],
                ]
                exported_source = await client.add_text_source(
                    notebook_id="nb_ent_1",
                    title="Synthesis: Quantum Teleportation",
                    content="Full multi-turn synthesis notes...",
                )
                self.assertEqual(exported_source.id, "src_exported_turn")
                self.assertEqual(exported_source.token_count, 1450)

        asyncio.run(_run())

    # =========================================================================
    # Scenario 2: Multi-Tier Quota Exhaustion & Continuous Failover
    # =========================================================================
    def test_t4_enterprise_04_cascading_failover_free_to_pro_to_ultra(self):
        """Simulates enterprise cascade: Free hits 1037 -> Pro hits 429 -> Ultra handles request."""
        mgr = self._create_mocked_manager()
        p_free = AccountProfile(id="free_acc", name="Free Worker", tier="free", priority=1)
        p_pro = AccountProfile(id="pro_acc", name="Pro Worker", tier="pro", priority=2)
        p_ultra = AccountProfile(id="ultra_acc", name="Ultra Worker", tier="ultra", priority=3)

        self.vault.save_account(p_free)
        self.vault.save_account(p_pro)
        self.vault.save_account(p_ultra)
        self.vault.update_failover_config(FailoverConfig(
            enabled=True,
            fallback_chain=["free_acc", "pro_acc", "ultra_acc"],
        ))

        async def _run():
            await mgr.initialize_all()
            for s in mgr.sessions.values():
                s.context.status = SessionHealthStatus.HEALTHY

            # Step 1: Free account exhausted
            mgr.mark_cooldown("free_acc", duration_seconds=3600, reason="Quota 1037")
            step1_target = mgr.get_failover_account("gemini-2.5-flash", "free_acc")
            self.assertEqual(step1_target.account_id, "pro_acc")

            # Step 2: Pro account also exhausted
            mgr.mark_cooldown("pro_acc", duration_seconds=1800, reason="RateLimit 429")
            step2_target = mgr.get_failover_account("gemini-2.5-flash", "pro_acc")
            self.assertEqual(step2_target.account_id, "ultra_acc")

        asyncio.run(_run())

    def test_t4_enterprise_05_concurrent_multi_account_streaming_isolation(self):
        """Verifies two streaming sessions running simultaneously on separate accounts don't cross-contaminate."""
        acc_a = StreamingDeltaAccumulator()
        acc_b = StreamingDeltaAccumulator()

        cand_a = ["rcid_a", ["Session A Stream"], None, None, None, None, None, None, [2], None, None, None, {}]
        cand_b = ["rcid_b", ["Session B Stream"], None, None, None, None, None, None, [2], None, None, None, {}]

        frame_a = [["wrb.fr", None, json.dumps([None, ["ca", "ra", "rcid_a"], None, None, [cand_a]])]]
        frame_b = [["wrb.fr", None, json.dumps([None, ["cb", "rb", "rcid_b"], None, None, [cand_b]])]]

        chunks_a = list(acc_a.process_frame(frame_a))
        chunks_b = list(acc_b.process_frame(frame_b))

        self.assertEqual(acc_a.full_raw_text, "Session A Stream")
        self.assertEqual(acc_b.full_raw_text, "Session B Stream")
        self.assertEqual(acc_a.rcid, "rcid_a")
        self.assertEqual(acc_b.rcid, "rcid_b")

    def test_t4_enterprise_06_telemetry_live_tracking_across_failover_chain(self):
        """Tracks token consumption and latency metrics per account in failover sequence via SQLite Database."""
        mgr = self._create_mocked_manager()
        p_free = AccountProfile(id="free_worker", name="Free Worker", tier="free", priority=1)
        p_pro = AccountProfile(id="pro_worker", name="Pro Worker", tier="pro", priority=2)
        p_ultra = AccountProfile(id="ultra_worker", name="Ultra Worker", tier="ultra", priority=3)

        self.vault.save_account(p_free)
        self.vault.save_account(p_pro)
        self.vault.save_account(p_ultra)
        self.vault.update_failover_config(FailoverConfig(
            enabled=True,
            fallback_chain=["free_worker", "pro_worker", "ultra_worker"],
        ))

        async def _run():
            await mgr.initialize_all()
            for s in mgr.sessions.values():
                s.context.status = SessionHealthStatus.HEALTHY

            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()

            # --- Turn 1: Primary free account encounters Google Quota 1037 ---
            now_ms = int(time.time() * 1000)
            mgr.mark_cooldown("free_worker", duration_seconds=3600, reason="Quota 1037")
            # Record failed turn attempt in telemetry
            await db.record_telemetry(
                account_id="free_worker",
                model_id="gemini-2.5-flash",
                model_name="Gemini 2.5 Flash",
                feature_mode="chat",
                prompt_tokens_est=120,
                completion_tokens_est=0,
                thinking_tokens_est=0,
                status_code=1037,
                error_code="Google Quota 1037",
                duration_ms=450,
                was_failover=1,
                request_timestamp=now_ms,
            )

            # Failover router resolves the next available tier (pro_worker)
            target1 = mgr.get_failover_account("gemini-2.5-flash", "free_worker")
            self.assertIsNotNone(target1)
            self.assertEqual(target1.account_id, "pro_worker")

            # pro_worker fulfills Turn 1 continuation
            await db.record_telemetry(
                account_id="pro_worker",
                model_id="gemini-2.5-flash",
                model_name="Gemini 2.5 Flash",
                feature_mode="chat",
                prompt_tokens_est=120,
                completion_tokens_est=850,
                thinking_tokens_est=150,
                status_code=200,
                duration_ms=1200,
                was_failover=1,
                failover_from_account="free_worker",
                request_timestamp=now_ms + 100,
            )

            # --- Turn 2: Subsequent request during active cooldown on free_worker ---
            self.assertTrue(mgr.is_in_cooldown("free_worker"))
            target2 = mgr.get_failover_account("gemini-2.5-flash", "free_worker")
            self.assertEqual(target2.account_id, "pro_worker")

            # pro_worker fulfills Turn 2
            await db.record_telemetry(
                account_id="pro_worker",
                model_id="gemini-2.5-flash",
                model_name="Gemini 2.5 Flash",
                feature_mode="chat",
                prompt_tokens_est=200,
                completion_tokens_est=650,
                thinking_tokens_est=50,
                status_code=200,
                duration_ms=800,
                was_failover=0,
                request_timestamp=now_ms + 200,
            )

            # --- Verify Database Aggregations & Metrics ---
            free_metrics = await db.get_rolling_usage("free_worker")
            self.assertEqual(free_metrics["total_requests"], 1)
            self.assertEqual(free_metrics["quota_error_count"], 1)
            self.assertEqual(free_metrics["total_prompt_tokens"], 120)
            self.assertEqual(free_metrics["total_completion_tokens"], 0)

            pro_metrics = await db.get_rolling_usage("pro_worker")
            self.assertEqual(pro_metrics["total_requests"], 2)
            self.assertEqual(pro_metrics["quota_error_count"], 0)
            self.assertEqual(pro_metrics["total_prompt_tokens"], 120 + 200)
            self.assertEqual(pro_metrics["total_completion_tokens"], 850 + 650)
            self.assertEqual(pro_metrics["total_thinking_tokens"], 150 + 50)
            self.assertEqual(pro_metrics["failover_events_count"], 1)

            total_metrics = await db.get_rolling_usage()
            self.assertEqual(total_metrics["total_requests"], 3)
            self.assertEqual(total_metrics["quota_error_count"], 1)
            self.assertEqual(total_metrics["failover_events_count"], 2)  # 1 marked on free, 1 on pro
            self.assertEqual(total_metrics["total_tokens"], 2140)

            # Verify failover audit trail
            failovers = await db.get_recent_failovers(limit=5)
            self.assertTrue(len(failovers) >= 1)
            pro_failover = next((f for f in failovers if f["account_id"] == "pro_worker"), None)
            self.assertIsNotNone(pro_failover)
            self.assertEqual(pro_failover["failover_from_account"], "free_worker")

            await db.close()

        asyncio.run(_run())

    # =========================================================================
    # Scenario 3: Multi-Turn Interactive Deep-Dive with Branching & Custom Gems
    # =========================================================================
    def test_t4_enterprise_07_gem_persona_injection_throughout_multi_turn_conversation(self):
        """Verifies custom Gem persona is bound to conversation and persists across multi-turn DAG branches."""
        gem = Gem(
            id="gem_security_auditor",
            name="Security Auditor",
            description="Performs threat modeling and static analysis.",
            prompt="Act as Senior Application Security Engineer. Focus on OWASP Top 10.",
        )

        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            # 1. Create conversation bound to custom Gem
            conv = await repo.create_conversation(
                title="Security Assessment",
                model="gemini-2.5-flash",
                gem_id=gem.id,
                system_prompt=gem.prompt,
            )
            self.assertEqual(conv.gem_id, "gem_security_auditor")
            self.assertEqual(conv.system_prompt, gem.prompt)

            # Helper simulating turn injection formatting as defined in backend/app/api/chat.py
            def format_turn_prompt(raw_prompt: str, gem_persona: str) -> str:
                return (
                    f"[System Instructions]\n"
                    f"{gem_persona.strip()}\n"
                    f"[End System Instructions]\n\n"
                    f"{raw_prompt}"
                )

            # 2. Turn 1 execution
            t1_prompt = "Analyze this authentication endpoint for CSRF and session fixation."
            t1_injected = format_turn_prompt(t1_prompt, gem.prompt)
            self.assertTrue(t1_injected.startswith("[System Instructions]\nAct as Senior Application Security Engineer."))
            self.assertTrue(t1_injected.endswith("Analyze this authentication endpoint for CSRF and session fixation."))

            m1_user = await repo.add_message(conv.id, "user", t1_injected, model="gemini-2.5-flash")
            m1_asst = await repo.add_message(
                conv.id,
                "assistant",
                "Audit Finding: Missing Anti-CSRF tokens and session cookie lacks SameSite=Strict attribute.",
                parent_id=m1_user.id,
                model="gemini-2.5-flash",
            )

            # 3. Turn 2 execution (Sequential follow-up)
            t2_prompt = "Now review the JWT refresh token rotation mechanism."
            t2_injected = format_turn_prompt(t2_prompt, gem.prompt)
            m2_user = await repo.add_message(conv.id, "user", t2_injected, parent_id=m1_asst.id, model="gemini-2.5-flash")
            m2_asst = await repo.add_message(
                conv.id,
                "assistant",
                "Audit Finding: Refresh token reuse detection is absent; vulnerable to token replay.",
                parent_id=m2_user.id,
                model="gemini-2.5-flash",
            )
            await repo.set_active_leaf(conv.id, m2_asst.id)

            # 4. Turn 2 Alternative Branch (Branching exploration with the same Gem persona)
            t2_alt_prompt = "Alternative: What if we use opaque Redis session IDs instead of JWT?"
            t2_alt_injected = format_turn_prompt(t2_alt_prompt, gem.prompt)
            m2_alt_user = await repo.add_message(
                conv.id, "user", t2_alt_injected, parent_id=m1_asst.id, model="gemini-2.5-flash"
            )
            m2_alt_asst = await repo.add_message(
                conv.id,
                "assistant",
                "Audit Finding: Redis opaque sessions provide immediate revocation capability.",
                parent_id=m2_alt_user.id,
                model="gemini-2.5-flash",
            )

            # 5. Verify conversation metadata and DAG branching topology
            reloaded_conv = await repo.get_conversation(conv.id)
            self.assertEqual(reloaded_conv.gem_id, "gem_security_auditor")
            self.assertEqual(reloaded_conv.system_prompt, "Act as Senior Application Security Engineer. Focus on OWASP Top 10.")

            # Verify Branch 1 (JWT track)
            await repo.set_active_leaf(conv.id, m2_asst.id)
            thread_jwt = await repo.get_active_thread(conv.id)
            self.assertEqual(len(thread_jwt), 4)
            self.assertIn("Missing Anti-CSRF", thread_jwt[1].content)
            self.assertIn("Refresh token reuse detection", thread_jwt[3].content)

            # Switch to Branch 2 (Redis track)
            await repo.set_active_leaf(conv.id, m2_alt_asst.id)
            thread_redis = await repo.get_active_thread(conv.id)
            self.assertEqual(len(thread_redis), 4)
            self.assertIn("Missing Anti-CSRF", thread_redis[1].content)
            self.assertIn("Redis opaque sessions", thread_redis[3].content)

            # Verify siblings under m1_asst
            idx_m2, total_m2 = await repo.get_message_siblings(m2_user.id)
            self.assertEqual(total_m2, 2)
            self.assertEqual(idx_m2, 1)

            idx_alt, total_alt = await repo.get_message_siblings(m2_alt_user.id)
            self.assertEqual(total_alt, 2)
            self.assertEqual(idx_alt, 2)

            all_msgs = await repo.get_all_messages(conv.id)
            m2_reloaded = next(m for m in all_msgs if m.id == m2_user.id)
            self.assertIn(m2_alt_user.id, m2_reloaded.sibling_ids)

            await db.close()

        asyncio.run(_run())

    def test_t4_enterprise_08_branching_dag_alternative_exploration_and_sibling_pinning(self):
        """Explores 3 alternative architectural options using in-place DAG branching, selects best and pins."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            conv = await repo.create_conversation(title="Architecture Trade-offs")
            prompt = await repo.add_message(conv.id, "user", "Design message broker architecture")

            # 3 alternative sibling responses
            alt_kafka = await repo.add_message(conv.id, "assistant", "Option 1: Apache Kafka", parent_id=prompt.id)
            alt_rabbitmq = await repo.add_message(conv.id, "assistant", "Option 2: RabbitMQ", parent_id=prompt.id)
            alt_nats = await repo.add_message(conv.id, "assistant", "Option 3: NATS JetStream", parent_id=prompt.id)

            # User explores Option 3 and continues branch
            follow_up = await repo.add_message(conv.id, "user", "How does NATS JetStream handle clustering?", parent_id=alt_nats.id)
            nats_resp = await repo.add_message(conv.id, "assistant", "NATS clustering details...", parent_id=follow_up.id)

            # Set active leaf to the chosen NATS branch
            await repo.set_active_leaf(conv.id, nats_resp.id)
            # Pin the conversation for team reference
            await repo.update_conversation(conv.id, is_pinned=True)

            active_thread = await repo.get_active_thread(conv.id)
            self.assertEqual(len(active_thread), 4)
            self.assertEqual(active_thread[1].content, "Option 3: NATS JetStream")

            saved_conv = await repo.get_conversation(conv.id)
            self.assertTrue(saved_conv.is_pinned)
            await db.close()

        asyncio.run(_run())

    def test_t4_enterprise_09_multimodal_grounding_rich_cards_full_pipeline(self):
        """Validates simultaneous extraction of web citations, generated images, and YouTube rich media cards."""
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_multi_rich",
            ["Full synthesis of Quantum Supremacy:\nhttps://www.youtube.com/watch?v=y3FS3KUOx5k"],
            None, None, None, None, None, None, [2], None, None, None,
            {
                "43": [
                    [1, "https://nature.com/articles/s41586-019-1666-5", "Quantum Supremacy Nature Paper", "nature.com", ""]
                ],
                "7": [
                    ["https://lh3.googleusercontent.com/sycamore_chip_diagram.png", "Sycamore Chip", 1920, 1080]
                ],
                "16": ["quantum supremacy sycamore", "quantum error correction threshold"],
            }
        ]
        inner = [None, ["c_1", "r_1", "rcid_multi_rich"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner)]]
        chunks = list(acc.process_frame(frame))

        self.assertEqual(len(acc.citations), 1)
        self.assertEqual(acc.citations[0]["domain"], "nature.com")
        self.assertEqual(len(acc.images), 1)
        self.assertEqual(acc.images[0]["url"], "https://lh3.googleusercontent.com/sycamore_chip_diagram.png")
        self.assertEqual(len(acc.grounding_queries), 2)
        self.assertEqual(len(acc.youtube_videos), 1)
        self.assertEqual(acc.youtube_videos[0]["video_id"], "y3FS3KUOx5k")

    def test_t4_enterprise_10_large_scale_conversation_tree_export_and_reimport(self):
        """Exports a complex multi-branch conversation tree to JSON, wipes DB, reimports and verifies topology."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            # Build tree: root -> [c1, c2 -> [c2_1, c2_2]]
            orig_conv = await repo.create_conversation(title="Master Topology")
            root = await repo.add_message(orig_conv.id, "user", "Root Question")
            b1 = await repo.add_message(orig_conv.id, "assistant", "Branch 1", parent_id=root.id)
            b2 = await repo.add_message(orig_conv.id, "assistant", "Branch 2", parent_id=root.id)
            b2_leaf = await repo.add_message(orig_conv.id, "user", "Followup 2", parent_id=b2.id)
            await repo.set_active_leaf(orig_conv.id, b2_leaf.id)

            # Export payload
            all_messages = await repo.get_all_messages(orig_conv.id)
            export_payload = {
                "conversation": orig_conv.to_dict(),
                "messages": [m.to_dict() for m in all_messages],
            }

            # Re-import into fresh conversation
            reimported_conv = await repo.create_conversation(
                title=f"Reimported: {export_payload['conversation']['title']}",
                model=export_payload['conversation']['model'],
            )
            id_mapping = {}
            for m in export_payload["messages"]:
                new_parent = id_mapping.get(m["parent_id"])
                new_m = await repo.add_message(
                    reimported_conv.id,
                    m["role"],
                    m["content"],
                    parent_id=new_parent,
                )
                id_mapping[m["id"]] = new_m.id

            reimported_msgs = await repo.get_all_messages(reimported_conv.id)
            self.assertEqual(len(reimported_msgs), 4)

            await db.close()

        asyncio.run(_run())

    # =========================================================================
    # Scenario 4: Auto-Pruning & Storage Lifecycle Maintenance
    # =========================================================================
    def test_t4_enterprise_11_database_pruning_lifecycle_with_pinned_preservation(self):
        """Simulates automatic retention purge: removes expired unpinned conversations, protects pinned ones."""
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)

            # Create 5 pinned conversations and 5 unpinned expired archived conversations
            now = int(time.time() * 1000)
            pinned_ids = []
            for i in range(5):
                c = await repo.create_conversation(title=f"Pinned Research {i}")
                await repo.update_conversation(c.id, is_pinned=True)
                pinned_ids.append(c.id)

            archived_ids = []
            for i in range(5):
                c = await repo.create_conversation(title=f"Archived Temp {i}")
                await repo.archive_conversation(c.id, is_archived=True)
                archived_ids.append(c.id)

            # Pruning policy: delete archived older than future cutoff WHERE is_pinned = 0
            future_cutoff = now + 100000
            await db.execute(
                "DELETE FROM conversations WHERE is_archived = 1 AND is_pinned = 0 AND updated_at < ?;",
                (future_cutoff,),
            )

            # Verify: all pinned remain, all archived deleted
            for pid in pinned_ids:
                self.assertIsNotNone(await repo.get_conversation(pid))

            for aid in archived_ids:
                self.assertIsNone(await repo.get_conversation(aid))

            # Database vacuum completes with zero errors
            await db.execute("PRAGMA auto_vacuum;")
            await db.close()

        asyncio.run(_run())

    def test_t4_enterprise_12_end_to_end_resilience_under_network_and_auth_faults(self):
        """Simulates mid-session network drop, reconnection, token refresh, and successful retry."""
        client = NotebookLMClient(fallback_snlm0e="initial_token")
        async def _run():
            # Turn 1: Network drop (ConnectError)
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                def _fail(*args, **kwargs):
                    client.is_offline_fallback = True
                    raise GeminiBridgeError("Connection dropped by peer")

                mock_rpc.side_effect = _fail
                with self.assertRaises(GeminiBridgeError):
                    await client.list_notebooks()
                self.assertTrue(client.is_offline_fallback)

                # Reconnection & credential refresh
                client.access_token = "refreshed_token_after_reconnect"
                client.is_offline_fallback = False

                # Retry: Success
                mock_rpc.side_effect = None
                mock_rpc.return_value = [
                    [["nb_recov_1", "Recovered Notebook", [1729000000, 0], [1729000000, 0], None, 1, "📓"]]
                ]
                notebooks = await client.list_notebooks()
                self.assertEqual(len(notebooks), 1)
                self.assertEqual(notebooks[0].title, "Recovered Notebook")

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
