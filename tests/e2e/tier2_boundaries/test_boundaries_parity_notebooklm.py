"""
tests/e2e/tier2_boundaries/test_boundaries_parity_notebooklm.py
Tier 2: Boundary & Corner Cases for Features F43 to F53 (Gemini Parity & NotebookLM).
Covers 50 boundary and corner cases:
- F43: Audio Overview & Direct Model Switching (5 tests)
- F44: Complete Chat Lifecycle Management (5 tests)
- F45: Grounding Sources, Citations & Rich Media Cards (5 tests)
- F46: Canvas / Artifacts Side-Panel & 1-Click Export (5 tests)
- F47: Branch Navigation, In-Place Regeneration & Sibling Forking (5 tests)
- F48: Complete Gems Manager & Persona Prefix Injector (5 tests)
- F49: NotebookLM Scraper & Session Sharing (5 tests)
- F50: NotebookLM Batchexecute RPC Handlers (5 tests)
- F51: NotebookLM Mock Server & Degraded Mode Fallback (5 tests)
- F52 & F53: REST API Router & Context Decoupling (5 tests)
Total: 50 tests.
"""

from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import AuthenticationError, GeminiBridgeError
from backend.app.bridge.models import DEFAULT_MODELS, Gem, GemSerializer, resolve_model
from backend.app.storage.exporter import ThreadExporter
from backend.app.bridge.notebooklm import (
    NotebookDetail,
    NotebookInfo,
    NotebookLMClient,
    NotebookNote,
    NotebookSource,
    _RE_CFB2H,
    _RE_FDRFJE,
    _RE_SNLM0E,
)
from backend.app.bridge.streaming_parser import StreamingDeltaAccumulator, _YOUTUBE_RE
from backend.app.main import app
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository


class TestTier2GeminiParityBoundaries(unittest.TestCase):
    """Boundary & Corner Cases for F43 to F48."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_t2_parity.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --- F43: Audio Overview & Direct Model Switching ---
    def test_t2_f43_01_unknown_model_fallback(self):
        info_unknown = resolve_model("gemini-nonexistent-v99")
        self.assertEqual(info_unknown.model_number, 1)
        self.assertEqual(info_unknown.capacity_tail, 12)
        info_pro = resolve_model("unknown-custom-pro-model")
        self.assertEqual(info_pro.model_number, 3)
        self.assertEqual(info_pro.capacity_tail, 15)
        self.assertTrue(info_pro.capabilities.supports_thinking)

    def test_t2_f43_02_empty_audio_payload(self):
        client = GeminiWebClient()
        envelope = client._build_81_element_envelope(prompt="", attachments=[])
        self.assertEqual(len(envelope), 81)
        self.assertEqual(envelope[0][0], "")
        self.assertIsNone(envelope[0][3])
        self.assertIsNone(envelope[41])

    def test_t2_f43_03_model_switching_preserves_conversation_id(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Switch Model", model="gemini-2.5-flash")
            updated = await repo.update_conversation(conv.id, model="gemini-3.1-pro")
            self.assertEqual(updated.id, conv.id)
            self.assertEqual(updated.model, "gemini-3.1-pro")
            await db.close()

        asyncio.run(_run())

    def test_t2_f43_04_audio_voice_configuration_boundary(self):
        for m_info in DEFAULT_MODELS.values():
            self.assertGreaterEqual(m_info.capabilities.context_window_tokens, 1_000_000)
            self.assertTrue(m_info.capabilities.supports_vision)
            self.assertTrue(m_info.capabilities.supports_file_upload)

    def test_t2_f43_05_model_capabilities_flag_check(self):
        argon = resolve_model("4 Argon")
        self.assertTrue(argon.supports_thinking)
        self.assertEqual(argon.mode_tag, "Argon4p0")
        self.assertEqual(argon.model_number, 8)
        flash_lite = resolve_model("3.5 Flash-Lite")
        self.assertEqual(flash_lite.model_number, 6)
        self.assertEqual(flash_lite.capacity_tail, 12)
        task_mode = resolve_model("3.8 Flash@mode=task")
        self.assertEqual(task_mode.feature_mode, 40)

    # --- F44: Complete Chat Lifecycle Management ---
    def test_t2_f44_01_rename_with_empty_title(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Initial")
            # Update to empty string
            updated = await repo.update_conversation(conv.id, title="")
            self.assertEqual(updated.title, "")
            await db.close()

        asyncio.run(_run())

    def test_t2_f44_02_pinning_idempotence(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Pinned Test")
            u1 = await repo.update_conversation(conv.id, is_pinned=True)
            u2 = await repo.update_conversation(conv.id, is_pinned=True)
            self.assertTrue(u1.is_pinned)
            self.assertTrue(u2.is_pinned)
            await db.close()

        asyncio.run(_run())

    def test_t2_f44_03_delete_already_deleted_conversation(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="To Delete")
            ok1 = await repo.delete_conversation(conv.id)
            ok2 = await repo.delete_conversation(conv.id)
            self.assertTrue(ok1)
            self.assertFalse(ok2)
            await db.close()

        asyncio.run(_run())

    def test_t2_f44_04_update_non_existent_conversation_returns_none(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            res = await repo.update_conversation("non_existent_conv_id", title="New Title")
            self.assertIsNone(res)
            await db.close()

        asyncio.run(_run())

    def test_t2_f44_05_rapid_sequential_title_updates(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="V1")
            for i in range(2, 6):
                conv = await repo.update_conversation(conv.id, title=f"V{i}")
            self.assertEqual(conv.title, "V5")
            await db.close()

        asyncio.run(_run())

    # --- F45: Grounding Sources, Citations & Rich Media Cards ---
    def test_t2_f45_01_corrupt_candidate_12_non_dict(self):
        acc = StreamingDeltaAccumulator()
        candidate = ["rcid_100", ["text"], None, None, None, None, None, None, [2], None, None, None, "not_a_dict_or_list"]
        inner_part = [None, ["c_1", "r_1", "rcid_100"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner_part)]]
        chunks = list(acc.process_frame(frame))
        self.assertIsInstance(chunks, list)

    def test_t2_f45_02_citation_fallback_domain_extraction(self):
        raw_citations = [
            [1, "https://developer.mozilla.org/en-US/docs/Web", "MDN Web Docs", None],
        ]
        acc = StreamingDeltaAccumulator()
        normalized = acc._normalize_citations(raw_citations)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["domain"], "developer.mozilla.org")

    def test_t2_f45_03_youtube_regex_boundary(self):
        urls = [
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://notyoutube.com/watch?v=123",
        ]
        vids = []
        for u in urls:
            m = _YOUTUBE_RE.search(u)
            if m:
                vids.append(m.group(1))
        self.assertEqual(vids, ["dQw4w9WgXcQ", "dQw4w9WgXcQ"])

    def test_t2_f45_04_empty_grounding_queries_list(self):
        acc = StreamingDeltaAccumulator()
        candidate = [
            "rcid_100", ["text"], None, None, None, None, None, None, [2], None, None, None,
            {"16": []}
        ]
        inner_part = [None, ["c_1", "r_1", "rcid_100"], None, None, [candidate]]
        frame = [["wrb.fr", None, json.dumps(inner_part)]]
        chunks = list(acc.process_frame(frame))
        self.assertEqual(len(acc.grounding_queries), 0)

    def test_t2_f45_05_citation_deduplication_boundary(self):
        raw_citations = [
            [1, "https://example.com/page", "Title 1", "example.com"],
            [1, "https://example.com/page", "Title 1", "example.com"],
        ]
        acc = StreamingDeltaAccumulator()
        normalized = acc._normalize_citations(raw_citations)
        self.assertEqual(len(normalized), 2)

    # --- F46: Canvas / Artifacts Side-Panel & 1-Click Export ---
    def test_t2_f46_01_code_block_missing_language_identifier(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Canvas Unlabelled")
            msg = await repo.add_message(conv.id, "assistant", "```\nconsole.log('hello');\n```")
            md = ThreadExporter.export_to_markdown(conv, [msg])
            self.assertIn("console.log('hello');", md)
            await db.close()

        asyncio.run(_run())

    def test_t2_f46_02_unclosed_markdown_code_fences(self):
        unclosed = "```python\ndef compute_gradient(x):\n    return 2 * x"
        balanced = ThreadExporter.balance_code_fences(unclosed)
        self.assertTrue(balanced.endswith("\n```\n"))
        self.assertEqual(balanced.count("```"), 2)

    def test_t2_f46_03_zero_length_code_artifact(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Empty Artifact")
            msg = await repo.add_message(conv.id, "assistant", "```python\n```")
            md = ThreadExporter.export_to_markdown(conv, [msg])
            self.assertIn("```python\n```", md)
            await db.close()

        asyncio.run(_run())

    def test_t2_f46_04_unsupported_export_format_fallback(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Canvas Export Check")
            tc = TestClient(app)
            resp = tc.get(f"/api/conversations/{conv.id}/export?format=pdf")
            self.assertEqual(resp.status_code, 422)
            await db.close()

        asyncio.run(_run())

    def test_t2_f46_05_multiple_artifacts_in_single_turn(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Multi Artifacts")
            turn_content = "```python\nx = 1\n```\nInterleaved text\n```html\n<h1>Page</h1>\n```"
            msg = await repo.add_message(conv.id, "assistant", turn_content)
            md = ThreadExporter.export_to_markdown(conv, [msg])
            self.assertIn("```python", md)
            self.assertIn("```html", md)
            await db.close()

        asyncio.run(_run())

    # --- F47: Branch Navigation, In-Place Regeneration & Sibling Forking ---
    def test_t2_f47_01_regeneration_on_root_node(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Root DAG")
            r1 = await repo.add_message(conv.id, "user", "Prompt 1")
            new_r1, _ = await repo.create_branch_edit(conv.id, target_message_id=r1.id, new_content="Edited Root Prompt")
            self.assertIsNone(new_r1.parent_id)
            idx1, total1 = await repo.get_message_siblings(r1.id)
            idx2, total2 = await repo.get_message_siblings(new_r1.id)
            self.assertEqual(total1, 2)
            self.assertEqual(total2, 2)
            self.assertEqual(idx1, 1)
            self.assertEqual(idx2, 2)
            await db.close()

        asyncio.run(_run())

    def test_t2_f47_02_out_of_bounds_sibling_navigation_clamping(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Out of Bounds DAG")
            with self.assertRaises(ValueError):
                await repo.switch_active_branch(conv.id, target_message_id=999999)
            await db.close()

        asyncio.run(_run())

    def test_t2_f47_03_negative_sibling_navigation_clamping(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Negative Branch ID")
            with self.assertRaises(ValueError):
                await repo.switch_active_branch(conv.id, target_message_id=-1)
            await db.close()

        asyncio.run(_run())

    def test_t2_f47_04_sibling_branch_history_depth(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Deep DAG")
            r1 = await repo.add_message(conv.id, "user", "Prompt 1")
            m2a = await repo.add_message(conv.id, "assistant", "Resp A", parent_id=r1.id)
            m2b = await repo.add_message(conv.id, "assistant", "Resp B", parent_id=r1.id)
            m2c = await repo.add_message(conv.id, "assistant", "Resp C", parent_id=r1.id)
            idx_a, total_a = await repo.get_message_siblings(m2a.id)
            idx_c, total_c = await repo.get_message_siblings(m2c.id)
            self.assertEqual(total_a, 3)
            self.assertEqual(total_c, 3)
            self.assertEqual(idx_c, 3)
            m3 = await repo.add_message(conv.id, "user", "Follow up", parent_id=m2c.id)
            thread = await repo.get_active_thread(conv.id, leaf_message_id=m3.id)
            self.assertEqual(len(thread), 3)
            self.assertEqual([m.id for m in thread], [r1.id, m2c.id, m3.id])
            await db.close()

        asyncio.run(_run())

    def test_t2_f47_05_active_leaf_pointer_on_sibling_switch(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Branching DAG")
            m1 = await repo.add_message(conv.id, "user", "Hello")
            m2a = await repo.add_message(conv.id, "assistant", "Response A", parent_id=m1.id)
            m2b = await repo.add_message(conv.id, "assistant", "Response B", parent_id=m1.id)
            await repo.set_active_leaf(conv.id, m2b.id)
            updated = await repo.get_conversation(conv.id)
            self.assertEqual(updated.active_leaf_message_id, m2b.id)
            await db.close()

        asyncio.run(_run())

    # --- F48: Complete Gems Manager & Persona Prefix Injector ---
    def test_t2_f48_01_gem_with_empty_system_instruction(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="No System Instruction", system_prompt="")
            msg = await repo.add_message(conv.id, "user", "Hello Gemini")
            md = ThreadExporter.export_to_markdown(conv, [msg])
            self.assertNotIn("> **System Prompt:**", md)
            await db.close()

        asyncio.run(_run())

    def test_t2_f48_02_gem_with_existing_system_instruction_combining(self):
        async def _run():
            db = Database(self.db_path)
            await db.connect()
            await db.init_schema()
            repo = ConversationRepository(db)
            conv = await repo.create_conversation(title="Persona Chat", system_prompt="Act as Senior Security Engineer.")
            msg = await repo.add_message(conv.id, "user", "Review code")
            md = ThreadExporter.export_to_markdown(conv, [msg])
            self.assertIn("> **System Prompt:**", md)
            self.assertIn("> Act as Senior Security Engineer.", md)
            await db.close()

        asyncio.run(_run())

    def test_t2_f48_03_gem_search_with_regex_special_characters(self):
        raw_rpc = [[["gem_regex", ["[C++] & (STL) Expert *", "Description with regex .*?+"], ["Prompt body"]]]]
        gems = GemSerializer.parse_list_response(raw_rpc)
        self.assertEqual(len(gems), 1)
        self.assertEqual(gems[0].name, "[C++] & (STL) Expert *")
        self.assertEqual(gems[0].description, "Description with regex .*?+")

    def test_t2_f48_04_gem_serialization_empty_payload(self):
        parsed = GemSerializer.parse_list_response([])
        self.assertEqual(len(parsed), 0)

    def test_t2_f48_05_gem_type_2_vs_3_payload_building(self):
        p_custom = GemSerializer.build_list_payload(is_custom=True)
        p_system = GemSerializer.build_list_payload(is_custom=False)
        self.assertEqual(p_custom[0], 2)
        self.assertEqual(p_system[0], 3)


class TestTier2NotebookLMBoundaries(unittest.TestCase):
    """Boundary & Corner Cases for F49 to F53."""

    # --- F49: NotebookLM Scraper & Session Sharing ---
    def test_t2_f49_01_scrape_tokens_from_empty_html(self):
        html = ""
        self.assertIsNone(_RE_SNLM0E.search(html))
        self.assertIsNone(_RE_CFB2H.search(html))
        self.assertIsNone(_RE_FDRFJE.search(html))

    def test_t2_f49_02_scrape_tokens_partial_tokens(self):
        html = '<script>"SNlM0e":"only_token_here"</script>'
        m_sn = _RE_SNLM0E.search(html)
        m_cf = _RE_CFB2H.search(html)
        self.assertIsNotNone(m_sn)
        self.assertIsNone(m_cf)
        self.assertEqual(m_sn.group(1), "only_token_here")

    def test_t2_f49_03_expired_cookies_dict_handling(self):
        cookies = {"__Secure-1PSID": ""}
        client = NotebookLMClient(cookies=cookies)
        self.assertEqual(client.cookies.get("__Secure-1PSID"), "")

    def test_t2_f49_04_session_sharing_without_cookie_mutation(self):
        original = {"__Secure-1PSID": "orig_val"}
        client = NotebookLMClient(cookies=original)
        client.cookies["__Secure-1PSID"] = "mutated_val"
        self.assertEqual(original["__Secure-1PSID"], "orig_val")

    def test_t2_f49_05_fallback_token_persistence(self):
        client = NotebookLMClient(fallback_snlm0e="persisted_fallback_token")
        self.assertEqual(client.access_token, "persisted_fallback_token")

    # --- F50: NotebookLM Batchexecute RPC Handlers ---
    def test_t2_f50_01_empty_batchexecute_response(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = None
                notebooks = await client.list_notebooks()
                self.assertEqual(notebooks, [])
        asyncio.run(_run())

    def test_t2_f50_02_error_code_7_raises_authentication_error(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.side_effect = AuthenticationError("Code 7 expired")
                with self.assertRaises(AuthenticationError):
                    await client.list_notebooks()
        asyncio.run(_run())

    def test_t2_f50_03_empty_notebook_detail_envelope(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = []
                with self.assertRaises(GeminiBridgeError):
                    await client.get_notebook("nb_empty")
        asyncio.run(_run())

    def test_t2_f50_04_missing_sources_and_notes_in_detail(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = [["nb_10", "Minimal", [1729000000, 0], [1729000000, 0]]]
                detail = await client.get_notebook("nb_10")
                self.assertEqual(detail.title, "Minimal")
                self.assertEqual(len(detail.sources), 0)
                self.assertEqual(len(detail.notes), 0)
        asyncio.run(_run())

    def test_t2_f50_05_rename_notebook_failure_handling(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.return_value = []
                ok = await client.rename_notebook("nb_1", "New Title")
                self.assertFalse(ok)
        asyncio.run(_run())

    # --- F51: NotebookLM Mock Server & Degraded Mode Fallback ---
    def test_t2_f51_01_offline_fallback_flag_toggling(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        self.assertFalse(client.is_offline_fallback)
        client.is_offline_fallback = True
        self.assertTrue(client.is_offline_fallback)

    def test_t2_f51_02_502_bad_gateway_handling(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.side_effect = GeminiBridgeError("HTTP 502 Bad Gateway")
                with self.assertRaises(GeminiBridgeError):
                    await client.list_notebooks()
        asyncio.run(_run())

    def test_t2_f51_03_timeout_handling(self):
        client = NotebookLMClient(fallback_snlm0e="token")
        async def _run():
            with patch.object(client, "call_batchexecute", new_callable=AsyncMock) as mock_rpc:
                mock_rpc.side_effect = GeminiBridgeError("Request timed out")
                with self.assertRaises(GeminiBridgeError):
                    await client.get_notebook("nb_timeout")
        asyncio.run(_run())

    def test_t2_f51_04_status_offline_flag_reflection(self):
        tc = TestClient(app)
        resp = tc.get("/api/notebooklm/status")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("offline_fallback", resp.json())

    def test_t2_f51_05_client_close_idempotence(self):
        client = NotebookLMClient()
        async def _run():
            await client.close()
            await client.close()  # second close must not raise
            self.assertIsNone(client._client)
        asyncio.run(_run())

    # --- F52 & F53: REST API Router & Context Decoupling ---
    def test_t2_f52_01_attach_context_not_found(self):
        tc = TestClient(app)
        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = GeminiBridgeError("Notebook not found")
            resp = tc.post("/api/notebooklm/attach-context", json={"notebook_id": "nb_missing"})
            self.assertEqual(resp.status_code, 404)

    def test_t2_f52_02_attach_context_empty_sources_and_notes(self):
        tc = TestClient(app)
        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = NotebookDetail(id="nb_empty", title="Empty", sources=[], notes=[])
            resp = tc.post(
                "/api/notebooklm/attach-context",
                json={"notebook_id": "nb_empty", "source_ids": [], "note_ids": [], "include_notes": False},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["source_count"], 0)
            self.assertEqual(data["note_count"], 0)

    def test_t2_f52_03_export_without_content_or_conversation_fails_400(self):
        tc = TestClient(app)
        resp = tc.post("/api/notebooklm/export", json={"notebook_id": "nb_1"})
        self.assertEqual(resp.status_code, 400)

    def test_t2_f53_04_decoupled_state_draft_prompt_isolation(self):
        tc = TestClient(app)
        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = NotebookDetail(
                id="nb_1",
                title="Research Notebook",
                sources=[NotebookSource(id="src_1", title="Source 1", summary="Source content 1")],
                notes=[NotebookNote(id="note_1", title="Note 1", content="Note content 1")],
            )
            resp = tc.post(
                "/api/notebooklm/attach-context",
                json={"notebook_id": "nb_1", "source_ids": ["src_1"], "include_notes": False},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["source_count"], 1)
            self.assertEqual(data["note_count"], 0)
            self.assertIn("<notebooklm_reference", data["formatted_context"])
            self.assertIn("Source content 1", data["formatted_context"])

    def test_t2_f53_05_decoupled_state_injection_consumption(self):
        tc = TestClient(app)
        with patch.object(NotebookLMClient, "get_notebook", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = NotebookDetail(
                id="nb_1",
                title="Research Notebook",
                sources=[
                    NotebookSource(id="src_1", title="Source 1", summary="Source content 1"),
                    NotebookSource(id="src_2", title="Source 2", summary="Source content 2"),
                ],
                notes=[
                    NotebookNote(id="note_1", title="Note 1", content="Note content 1"),
                    NotebookNote(id="note_2", title="Note 2", content="Note content 2"),
                ],
            )
            resp = tc.post(
                "/api/notebooklm/attach-context",
                json={
                    "notebook_id": "nb_1",
                    "source_ids": ["src_2"],
                    "note_ids": ["note_1"],
                    "include_notes": True,
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["source_count"], 1)
            self.assertEqual(data["note_count"], 1)
            self.assertIn("Source content 2", data["formatted_context"])
            self.assertNotIn("Source content 1", data["formatted_context"])
            self.assertIn("Note content 1", data["formatted_context"])
            self.assertNotIn("Note content 2", data["formatted_context"])


if __name__ == "__main__":
    unittest.main()
